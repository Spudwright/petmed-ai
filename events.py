"""crittr.ai — Event tracking + funnel (Phase 6.2).

Purpose
-------
A single tiny `events` table + a JS-callable ingest endpoint + a
funnel dashboard. Lets us answer: of the N anon chats today, how many
got a verdict, clicked a partner, or signed up?

Design goals
------------
  * Schema-light: one wide table, JSONB meta for event-specific fields.
  * No PII by default: we hash IP+UA into a fingerprint; user_id is
    only set for logged-in events.
  * Fire-and-forget from the frontend: POST /api/events with
    {name, meta?}. No response payload the JS needs to wait on.

Event names (client-side JS fires these)
----------------------------------------
  anon_chat_message_sent    meta: {hint?}
  verdict_shown             meta: {verdict}
  partner_cta_clicked       meta: {partner, verdict?}
  signup_nudge_clicked      meta: {}
  signup_completed          meta: {} (fired server-side after signup)

Public API
----------
  register_event_routes(app, q) -> None
    Wires POST /api/events (ingest) and GET /admin/funnel (dashboard).
  log_event(q, name, user_id=None, fingerprint=None, meta=None)
    Server-side helper so backend routes can log events too.
"""
import os
import json
import hashlib
import logging
from functools import wraps
from flask import request, jsonify, Response, render_template_string

log = logging.getLogger("crittr.events")


# ---------------------------------------------------------------
# Schema
# ---------------------------------------------------------------
def _ensure_schema(q):
    try:
        q(
            """
            CREATE TABLE IF NOT EXISTS events (
              id          BIGSERIAL PRIMARY KEY,
              created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              name        TEXT NOT NULL,
              user_id     BIGINT,
              fingerprint TEXT,
              meta        JSONB NOT NULL DEFAULT '{}'::jsonb
            );
            CREATE INDEX IF NOT EXISTS ix_events_created ON events(created_at DESC);
            CREATE INDEX IF NOT EXISTS ix_events_name    ON events(name);
            CREATE INDEX IF NOT EXISTS ix_events_user    ON events(user_id)
              WHERE user_id IS NOT NULL;
            CREATE INDEX IF NOT EXISTS ix_events_fp      ON events(fingerprint)
              WHERE fingerprint IS NOT NULL;
            """,
            fetch=False,
        )
    except Exception as e:
        log.warning("[events] schema ensure failed: %s", e)


# ---------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------
def _fingerprint():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "")
    ip = ip.split(",")[0].strip()
    ua = request.headers.get("User-Agent", "")
    return hashlib.sha256((ip + "|" + ua).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------
# Server-side helper
# ---------------------------------------------------------------
# Event names we'll accept. Typo-proofs the ingest route and keeps the
# dashboard joins predictable.
_ALLOWED = {
    "anon_chat_message_sent",
    "verdict_shown",
    "partner_cta_clicked",
    "signup_nudge_clicked",
    "signup_completed",
    "login_success",
    "pet_created",
    "chat_message_sent",
}


def log_event(q, name, user_id=None, fingerprint=None, meta=None):
    """Insert one row. Swallows errors — never block a real request."""
    if name not in _ALLOWED:
        log.warning("[events] rejected unknown name: %s", name)
        return
    try:
        q(
            "INSERT INTO events (name, user_id, fingerprint, meta) "
            "VALUES (%s, %s, %s, %s::jsonb);",
            (name, user_id, fingerprint, json.dumps(meta or {})),
            fetch=False,
        )
    except Exception as e:
        log.warning("[events] insert failed (%s): %s", name, e)


# ---------------------------------------------------------------
# Auth (reuse pattern from admin_dashboard.py)
# ---------------------------------------------------------------
def _basic_auth_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        user = os.environ.get("ADMIN_USER")
        pw = os.environ.get("ADMIN_PASS")
        if not user or not pw:
            return ("Not Found", 404)
        auth = request.authorization
        if not auth or auth.username != user or auth.password != pw:
            return Response(
                "Authentication required", 401,
                {"WWW-Authenticate": 'Basic realm="crittr-admin"'},
            )
        return view(*a, **kw)
    return wrapped


# ---------------------------------------------------------------
# Funnel computation
# ---------------------------------------------------------------
def _count_by_name(q, hours):
    try:
        rows = q(
            "SELECT name, COUNT(*) AS n "
            "FROM events "
            "WHERE created_at > NOW() - (%s || ' hours')::INTERVAL "
            "GROUP BY name;",
            (str(hours),),
        ) or []
        return {r["name"]: int(r["n"]) for r in rows}
    except Exception as e:
        log.debug("[events] funnel count failed: %s", e)
        return {}


def _verdict_breakdown(q, hours):
    """How many verdict_shown events were each kind."""
    try:
        rows = q(
            "SELECT meta->>'verdict' AS verdict, COUNT(*) AS n "
            "FROM events "
            "WHERE name = 'verdict_shown' "
            "  AND created_at > NOW() - (%s || ' hours')::INTERVAL "
            "GROUP BY 1;",
            (str(hours),),
        ) or []
        return {r["verdict"] or "(none)": int(r["n"]) for r in rows}
    except Exception as e:
        log.debug("[events] verdict breakdown failed: %s", e)
        return {}


# ---------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------
_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>crittr admin — funnel</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>
    :root {
      --bg:#FBF7EE; --ink:#2A2A2A; --muted:#6B6B6B;
      --er:#C84A3A; --vet:#D9A23A; --safe:#6FA26F; --none:#9A9A9A;
      --card:#FFFFFF; --line:#E7E1D2; --bar:#6FA26F;
    }
    body { margin:0; font-family:Inter,system-ui,sans-serif;
           background:var(--bg); color:var(--ink); }
    header { padding:24px 32px; border-bottom:1px solid var(--line); }
    header h1 { margin:0; font-family:'Fraunces',serif; font-weight:500; }
    main { padding:24px 32px; max-width:900px; }
    section { margin-bottom:36px; }
    section h2 { font-family:'Fraunces',serif; font-weight:500;
                 font-size:20px; margin:0 0 12px 0; }
    .window-tabs { display:flex; gap:8px; margin-bottom:16px; }
    .window-tabs a { padding:6px 14px; border:1px solid var(--line);
                     border-radius:999px; text-decoration:none; color:var(--ink);
                     font-size:14px; background:var(--card); }
    .window-tabs a.active { background:var(--ink); color:white; border-color:var(--ink); }
    .step { display:grid; grid-template-columns:220px 80px 1fr; gap:12px;
            align-items:center; padding:8px 0;
            border-bottom:1px dashed var(--line); }
    .step:last-child { border-bottom:none; }
    .step-name { font-weight:500; }
    .step-count { font-variant-numeric:tabular-nums; font-weight:600; }
    .step-bar { height:12px; background:var(--line); border-radius:6px;
                overflow:hidden; }
    .step-bar-fill { height:100%; background:var(--bar); }
    .step-meta { grid-column: 2 / 4; color:var(--muted); font-size:13px;
                 margin-top:-4px; }
    .card { background:var(--card); border:1px solid var(--line);
            border-radius:10px; padding:16px; }
    table { width:100%; border-collapse:collapse; }
    td { padding:6px 4px; border-bottom:1px dashed var(--line); font-size:14px; }
    td:last-child { text-align:right; font-variant-numeric:tabular-nums; }
    .dot { display:inline-block; width:10px; height:10px; border-radius:50%;
           margin-right:8px; vertical-align:middle; }
    .dot.er { background:var(--er); }
    .dot.vet { background:var(--vet); }
    .dot.safe { background:var(--safe); }
    .dot.none { background:var(--none); }
  </style>
</head>
<body>
  <header>
    <h1>Conversion funnel</h1>
  </header>
  <main>
    <div class="window-tabs">
      {% for w in window_options %}
        <a href="?window={{ w.key }}"
           class="{{ 'active' if w.key == selected else '' }}">{{ w.label }}</a>
      {% endfor %}
    </div>

    <section class="card">
      <h2>Steps ({{ selected_label }})</h2>
      {% for step in steps %}
        <div class="step">
          <div class="step-name">{{ step.label }}</div>
          <div class="step-count">{{ step.n }}</div>
          <div class="step-bar">
            <div class="step-bar-fill"
                 style="width:{{ step.pct_of_first }}%"></div>
          </div>
          {% if step.conversion_from_prev is not none %}
          <div class="step-meta">
            {{ step.conversion_from_prev }}% of previous step
            {% if step.delta_from_prev %}
              ({{ step.delta_from_prev }} lost)
            {% endif %}
          </div>
          {% endif %}
        </div>
      {% endfor %}
    </section>

    <section class="card">
      <h2>Verdicts shown — breakdown ({{ selected_label }})</h2>
      <table>
        {% for label, n in verdict_rows %}
        <tr>
          <td><span class="dot {{ label|verdict_class }}"></span>{{ label }}</td>
          <td>{{ n }}</td>
        </tr>
        {% endfor %}
      </table>
    </section>
  </main>
</body>
</html>"""

_VERDICT_CLASS = {"ER NOW": "er", "VET TOMORROW": "vet",
                  "SAFE AT HOME": "safe", "(none)": "none"}


# ---------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------
def register_event_routes(app, q):
    """Wire POST /api/events + GET /admin/funnel.

    Args:
        app: Flask app
        q:   query helper(sql, params=(), fetch=True) -> list[dict]
    """
    _ensure_schema(q)
    app.jinja_env.filters["verdict_class"] = \
        app.jinja_env.filters.get("verdict_class") \
        or (lambda v: _VERDICT_CLASS.get(v, "none"))

    @app.route("/api/events", methods=["POST"])
    def api_events():
        data = request.get_json(silent=True) or {}
        name = (data.get("name") or "").strip()
        meta = data.get("meta") or {}
        if not isinstance(meta, dict):
            meta = {}
        # Shallow sanitize: cap total size to avoid blob abuse.
        try:
            if len(json.dumps(meta)) > 4096:
                return jsonify({"error": "meta too large"}), 413
        except Exception:
            meta = {}
        # user_id is trusted only if Flask session has it; otherwise None.
        user_id = None
        try:
            from flask import session as _s
            user_id = _s.get("user_id")
        except Exception:
            pass
        fp = _fingerprint()
        log_event(q, name, user_id=user_id, fingerprint=fp, meta=meta)
        # Intentionally terse — this is fire-and-forget for the client.
        return ("", 204)

    @app.route("/admin/funnel")
    @_basic_auth_required
    def admin_funnel():
        window_options = [
            {"key": "24h", "label": "Last 24h", "hours": 24},
            {"key": "7d",  "label": "Last 7d",  "hours": 24 * 7},
            {"key": "30d", "label": "Last 30d", "hours": 24 * 30},
        ]
        selected = request.args.get("window", "7d")
        win = next((w for w in window_options if w["key"] == selected),
                   window_options[1])
        counts = _count_by_name(q, win["hours"])
        # Funnel definition (in order)
        funnel_steps = [
            ("Chat message sent",
             counts.get("anon_chat_message_sent", 0)
             + counts.get("chat_message_sent", 0)),
            ("Verdict shown",          counts.get("verdict_shown", 0)),
            ("Partner CTA clicked",    counts.get("partner_cta_clicked", 0)),
            ("Signup nudge clicked",   counts.get("signup_nudge_clicked", 0)),
            ("Signup completed",       counts.get("signup_completed", 0)),
        ]
        first = funnel_steps[0][1] or 1
        steps = []
        prev_n = None
        for label, n in funnel_steps:
            conv = None
            delta = 0
            if prev_n is not None and prev_n > 0:
                conv = round(100 * n / prev_n, 1)
                delta = prev_n - n
            steps.append({
                "label": label,
                "n": n,
                "pct_of_first": round(100 * n / first, 1),
                "conversion_from_prev": conv,
                "delta_from_prev": delta,
            })
            prev_n = n

        vbreak = _verdict_breakdown(q, win["hours"])
        verdict_rows = [
            ("ER NOW",       vbreak.get("ER NOW", 0)),
            ("VET TOMORROW", vbreak.get("VET TOMORROW", 0)),
            ("SAFE AT HOME", vbreak.get("SAFE AT HOME", 0)),
            ("(none)",       vbreak.get("(none)", 0)),
        ]
        return render_template_string(
            _HTML,
            window_options=window_options,
            selected=selected,
            selected_label=win["label"],
            steps=steps,
            verdict_rows=verdict_rows,
        )
