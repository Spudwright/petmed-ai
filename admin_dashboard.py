"""crittr.ai — Admin analytics dashboard (Phase 6.1).

Purpose
-------
A password-protected HTML page at GET /admin/verdicts that lets you
eyeball the health of the triage system without opening psql:

  * Verdict distribution (ER NOW / VET TOMORROW / SAFE AT HOME / none)
    bucketed 24h / 7d / 30d.
  * Top words by verdict (crude tf over the user-submitted message —
    useful for spotting patterns like "grapes -> always ER NOW").
  * The 20 most recent ER NOW verdicts with the full submitted message,
    so you can sanity-check whether the model is over-escalating.

Data sources
------------
  * anon_chats — populated by anon_chat.py (public hero widget).
  * pet_chat_messages — populated by pets_routes-v2.py (logged-in chat).
    We read both when present; schema drift is tolerated.

Auth
----
Protected by HTTP basic auth against env ADMIN_USER / ADMIN_PASS.
If either env is unset, the route 404s (fail closed, not open).

Public API
----------
  register_admin_dashboard(app, q) -> None
    Wires GET /admin/verdicts (HTML) and GET /admin/verdicts.json.
"""
import os
import re
import logging
from collections import Counter
from functools import wraps
from flask import request, Response, jsonify, render_template_string

log = logging.getLogger("crittr.admin")

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "is", "are", "was", "were",
    "my", "your", "his", "her", "its", "our", "their", "i", "you",
    "he", "she", "we", "they", "it", "to", "of", "in", "on", "at",
    "for", "with", "by", "from", "as", "that", "this", "these",
    "those", "been", "being", "be", "has", "have", "had", "do",
    "does", "did", "will", "would", "should", "could", "may", "might",
    "can", "not", "no", "so", "if", "then", "than", "also", "just",
    "very", "really", "some", "any", "all", "about", "into", "out",
    "over", "under", "again", "more", "most", "less", "up", "down",
    "me", "him", "them", "am", "who", "what", "when", "where", "why",
    "how", "get", "got", "like", "now", "today", "yesterday", "night",
    "day", "time", "one", "two", "three", "mg", "ml",
}

_TOKEN_RE = re.compile(r"[a-z][a-z\-']{2,}")


def _tokens(text):
    if not text:
        return []
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


# ---------------------------------------------------------------
# Basic auth wrapper
# ---------------------------------------------------------------
def _basic_auth_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        user = os.environ.get("ADMIN_USER")
        pw = os.environ.get("ADMIN_PASS")
        if not user or not pw:
            # Fail closed: pretend the route doesn't exist.
            return ("Not Found", 404)
        auth = request.authorization
        if not auth or auth.username != user or auth.password != pw:
            return Response(
                "Authentication required",
                401,
                {"WWW-Authenticate": 'Basic realm="crittr-admin"'},
            )
        return view(*a, **kw)
    return wrapped


# ---------------------------------------------------------------
# Queries
# ---------------------------------------------------------------
_VERDICT_LABELS = ("ER NOW", "VET TOMORROW", "SAFE AT HOME", "(none)")


def _verdict_distribution(q, hours):
    """Return {label: count} over the last N hours, merging anon_chats
    + pet_chat_messages (whichever tables exist)."""
    counts = Counter()
    # anon_chats
    try:
        rows = q(
            "SELECT COALESCE(verdict, '(none)') AS v, COUNT(*) AS n "
            "FROM anon_chats "
            "WHERE created_at > NOW() - (%s || ' hours')::INTERVAL "
            "GROUP BY 1;",
            (str(hours),),
        ) or []
        for r in rows:
            counts[r["v"]] += int(r["n"])
    except Exception as e:
        log.debug("[admin] anon_chats distribution skipped: %s", e)
    # pet_chat_messages (only assistant rows carry verdicts)
    try:
        rows = q(
            "SELECT COALESCE(verdict, '(none)') AS v, COUNT(*) AS n "
            "FROM pet_chat_messages "
            "WHERE role = 'assistant' "
            "  AND created_at > NOW() - (%s || ' hours')::INTERVAL "
            "GROUP BY 1;",
            (str(hours),),
        ) or []
        for r in rows:
            counts[r["v"]] += int(r["n"])
    except Exception as e:
        log.debug("[admin] pet_chat_messages distribution skipped: %s", e)
    return counts


def _top_terms_by_verdict(q, hours, top_n=10):
    """For each verdict, return [(term, count), ...] from the user
    messages that produced that verdict over the last N hours.

    Uses anon_chats (owner message + assistant verdict on same row) —
    pet_chat_messages has user/assistant on separate rows and stitching
    them is a bigger job; skip for now.
    """
    out = {}
    try:
        rows = q(
            "SELECT COALESCE(verdict, '(none)') AS v, message "
            "FROM anon_chats "
            "WHERE created_at > NOW() - (%s || ' hours')::INTERVAL;",
            (str(hours),),
        ) or []
    except Exception as e:
        log.debug("[admin] top terms skipped: %s", e)
        return {}
    buckets = {}
    for r in rows:
        buckets.setdefault(r["v"], Counter()).update(_tokens(r["message"]))
    for verdict, ctr in buckets.items():
        out[verdict] = ctr.most_common(top_n)
    return out


def _recent_er_now(q, limit=20):
    """Most recent ER NOW verdicts with the original message. Tries
    anon_chats first, then logged-in transcripts."""
    out = []
    try:
        rows = q(
            "SELECT created_at, message, reply, 'anon' AS source "
            "FROM anon_chats "
            "WHERE verdict = 'ER NOW' "
            "ORDER BY created_at DESC LIMIT %s;",
            (limit,),
        ) or []
        out.extend(rows)
    except Exception as e:
        log.debug("[admin] anon ER NOW skipped: %s", e)
    # Best-effort on logged-in side: just grab the assistant row, we
    # won't attempt to join the preceding user turn.
    try:
        rows = q(
            "SELECT created_at, content AS message, content AS reply, 'logged_in' AS source "
            "FROM pet_chat_messages "
            "WHERE verdict = 'ER NOW' AND role = 'assistant' "
            "ORDER BY created_at DESC LIMIT %s;",
            (limit,),
        ) or []
        out.extend(rows)
    except Exception as e:
        log.debug("[admin] logged-in ER NOW skipped: %s", e)
    # Re-sort the merged list and cap
    out.sort(key=lambda r: r.get("created_at") or 0, reverse=True)
    return out[:limit]


# ---------------------------------------------------------------
# HTML
# ---------------------------------------------------------------
_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>crittr admin — verdicts</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>
    :root {
      --bg:#FBF7EE; --ink:#2A2A2A; --muted:#6B6B6B;
      --er:#C84A3A; --vet:#D9A23A; --safe:#6FA26F; --none:#9A9A9A;
      --card:#FFFFFF; --line:#E7E1D2;
    }
    body { margin:0; font-family:Inter,system-ui,sans-serif;
           background:var(--bg); color:var(--ink); }
    header { padding:24px 32px; border-bottom:1px solid var(--line); }
    header h1 { margin:0; font-family:'Fraunces',serif; font-weight:500; }
    header .sub { color:var(--muted); margin-top:4px; font-size:14px; }
    main { padding:24px 32px; max-width:1100px; }
    section { margin-bottom:36px; }
    section h2 { font-family:'Fraunces',serif; font-weight:500;
                 font-size:20px; margin:0 0 12px 0; }
    .row { display:grid; grid-template-columns:repeat(3, 1fr); gap:16px; }
    .card { background:var(--card); border:1px solid var(--line);
            border-radius:10px; padding:16px; }
    .card h3 { margin:0 0 8px 0; font-size:14px; color:var(--muted);
               font-weight:500; letter-spacing:0.04em;
               text-transform:uppercase; }
    .verdict-row { display:flex; align-items:center;
                   justify-content:space-between; padding:6px 0;
                   border-bottom:1px dashed var(--line); }
    .verdict-row:last-child { border-bottom:none; }
    .dot { display:inline-block; width:10px; height:10px; border-radius:50%;
           margin-right:8px; vertical-align:middle; }
    .dot.er { background:var(--er); }
    .dot.vet { background:var(--vet); }
    .dot.safe { background:var(--safe); }
    .dot.none { background:var(--none); }
    .count { font-weight:600; font-variant-numeric:tabular-nums; }
    .terms { display:flex; flex-wrap:wrap; gap:6px; margin-top:8px; }
    .term { background:var(--bg); border:1px solid var(--line);
            border-radius:999px; padding:3px 10px; font-size:12px;
            color:var(--muted); }
    .term b { color:var(--ink); }
    table { width:100%; border-collapse:collapse; }
    th, td { text-align:left; padding:8px 6px; border-bottom:1px solid var(--line);
             font-size:14px; vertical-align:top; }
    th { color:var(--muted); font-weight:500; }
    td.ts { white-space:nowrap; color:var(--muted); width:160px; }
    td.src { color:var(--muted); width:80px; }
    .empty { color:var(--muted); font-style:italic; padding:12px 0; }
  </style>
</head>
<body>
  <header>
    <h1>Verdict analytics</h1>
    <div class="sub">Reads anon_chats + pet_chat_messages. Updated on page load.</div>
  </header>
  <main>
    <section>
      <h2>Verdict distribution</h2>
      <div class="row">
      {% for window in windows %}
        <div class="card">
          <h3>Last {{ window.label }}</h3>
          {% set total = window.total %}
          {% for v in verdict_labels %}
            {% set n = window.counts.get(v, 0) %}
            <div class="verdict-row">
              <div>
                <span class="dot {{ v|verdict_class }}"></span>
                {{ v }}
              </div>
              <div>
                <span class="count">{{ n }}</span>
                <span style="color:var(--muted)">
                  ({{ (100 * n / total) | round(1) if total else 0 }}%)
                </span>
              </div>
            </div>
          {% endfor %}
        </div>
      {% endfor %}
      </div>
    </section>

    <section>
      <h2>Top terms by verdict (last 7 days)</h2>
      <div class="row">
      {% for v in verdict_labels %}
        <div class="card">
          <h3><span class="dot {{ v|verdict_class }}"></span>{{ v }}</h3>
          {% set terms = top_terms.get(v, []) %}
          {% if terms %}
            <div class="terms">
              {% for term, count in terms %}
                <span class="term"><b>{{ term }}</b> · {{ count }}</span>
              {% endfor %}
            </div>
          {% else %}
            <div class="empty">No data yet.</div>
          {% endif %}
        </div>
      {% endfor %}
      </div>
    </section>

    <section>
      <h2>Recent ER NOW verdicts (20 most recent)</h2>
      {% if recent_er %}
      <table>
        <thead>
          <tr><th>When</th><th>Source</th><th>Message / reply</th></tr>
        </thead>
        <tbody>
          {% for row in recent_er %}
          <tr>
            <td class="ts">{{ row.created_at }}</td>
            <td class="src">{{ row.source }}</td>
            <td>
              <div><b>msg:</b> {{ row.message }}</div>
              {% if row.reply and row.reply != row.message %}
                <div style="margin-top:4px; color:var(--muted)">
                  <b>reply:</b> {{ row.reply[:280] }}{% if row.reply|length > 280 %}…{% endif %}
                </div>
              {% endif %}
            </td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
      {% else %}
      <div class="empty">No ER NOW verdicts yet.</div>
      {% endif %}
    </section>
  </main>
</body>
</html>"""


_VERDICT_CSS_CLASS = {
    "ER NOW": "er",
    "VET TOMORROW": "vet",
    "SAFE AT HOME": "safe",
    "(none)": "none",
}


# ---------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------
def register_admin_dashboard(app, q):
    """Wire GET /admin/verdicts (HTML) and GET /admin/verdicts.json.

    Args:
        app: Flask app
        q:   query helper(sql, params=(), fetch=True) -> list[dict]
    """
    # Register a Jinja filter so the template can color-code verdicts.
    app.jinja_env.filters["verdict_class"] = lambda v: _VERDICT_CSS_CLASS.get(v, "none")

    @app.route("/admin/verdicts")
    @_basic_auth_required
    def admin_verdicts():
        windows = []
        for label, hours in (("24h", 24), ("7d", 24 * 7), ("30d", 24 * 30)):
            counts = _verdict_distribution(q, hours)
            total = sum(counts.values())
            windows.append({"label": label, "counts": counts, "total": total})
        top_terms = _top_terms_by_verdict(q, hours=24 * 7, top_n=10)
        recent_er = _recent_er_now(q, limit=20)
        return render_template_string(
            _HTML,
            windows=windows,
            verdict_labels=_VERDICT_LABELS,
            top_terms=top_terms,
            recent_er=recent_er,
        )

    @app.route("/admin/verdicts.json")
    @_basic_auth_required
    def admin_verdicts_json():
        windows = {}
        for label, hours in (("24h", 24), ("7d", 24 * 7), ("30d", 24 * 30)):
            counts = _verdict_distribution(q, hours)
            windows[label] = {
                "counts": dict(counts),
                "total": sum(counts.values()),
            }
        return jsonify({
            "windows": windows,
            "top_terms_7d": {
                k: [{"term": t, "count": c} for (t, c) in v]
                for k, v in _top_terms_by_verdict(q, 24 * 7).items()
            },
            "recent_er_now": [
                {
                    "created_at": str(r.get("created_at")),
                    "source": r.get("source"),
                    "message": r.get("message"),
                    "reply": (r.get("reply") or "")[:280],
                }
                for r in _recent_er_now(q, 20)
            ],
        })
