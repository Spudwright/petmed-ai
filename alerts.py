"""crittr.ai — Hourly quality alerting (Phase 6.4).

Purpose
-------
Run this once an hour from cron / APScheduler. It sanity-checks the
LLM pipeline and surfaces problems before users find them.

Checks
------
  1. LLM provider responding — calls has_provider() and a 1-shot ping.
  2. Verdict coverage — % of assistant replies on anon_chats with NO
     verdict in the last hour. Alerts if > 20% (alertable threshold).
  3. Token / length anomalies — average reply length in last hour vs
     average in preceding 24h. Alerts on 3x jump (token-leak proxy).
  4. Rate-limit pressure — counts anon_chats rows that came from a
     single fingerprint > 20 times in 1 hour.
  5. Fallback events — counts rows in llm_fallback_events table over
     the last hour; alerts if > 10.

Alerting channels
-----------------
  - If SLACK_ALERT_WEBHOOK is set, POST a JSON payload.
  - If ALERT_EMAIL is set and emails.py is available, send a plain email.
  - Always logs to stdout.

Usage
-----
    python alerts.py             # runs once, exits
    python alerts.py --dry-run   # prints what it would alert, doesn't send

Importable:
    from alerts import run_checks
    summary = run_checks(q)
"""
import os
import sys
import json
import logging
from urllib import request as urllib_request
from urllib.error import URLError

log = logging.getLogger("crittr.alerts")


# ---------------------------------------------------------------
# Schema (lazy — also created by llm_client + anon_chat + events)
# ---------------------------------------------------------------
def _ensure_fallback_table(q):
    try:
        q(
            """
            CREATE TABLE IF NOT EXISTS llm_fallback_events (
              id         BIGSERIAL PRIMARY KEY,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              provider   TEXT,
              stage      TEXT,
              error      TEXT
            );
            CREATE INDEX IF NOT EXISTS ix_llm_fb_created
              ON llm_fallback_events(created_at DESC);
            """,
            fetch=False,
        )
    except Exception as e:
        log.warning("ensure fallback table failed: %s", e)


def record_fallback(q, provider, stage, error):
    """Wire this into llm_client.set_fallback_observer(...) at boot."""
    _ensure_fallback_table(q)
    try:
        q(
            "INSERT INTO llm_fallback_events (provider, stage, error) "
            "VALUES (%s, %s, %s);",
            (provider, stage, error),
            fetch=False,
        )
    except Exception as e:
        log.warning("record_fallback insert failed: %s", e)


# ---------------------------------------------------------------
# Individual checks — each returns (ok:bool, message:str, detail:dict)
# ---------------------------------------------------------------
def check_llm_provider():
    try:
        from llm_client import has_provider, generate_chat_reply
    except Exception as e:
        return False, f"llm_client import failed: {e}", {}
    if not has_provider():
        return False, "no LLM provider configured", {}
    try:
        reply = generate_chat_reply(
            "You are a health check. Reply with the single word: ok.",
            [],
            "ping",
        )
        if not reply or len(reply) > 100:
            return False, f"provider reply looked wrong: {reply[:80]!r}", {
                "reply": reply[:200],
            }
        return True, "provider reachable", {"sample_reply": reply[:80]}
    except Exception as e:
        return False, f"provider call raised: {e}", {}


def check_verdict_coverage(q, miss_threshold_pct=20):
    try:
        rows = q(
            """
            SELECT
              COUNT(*)                                   AS total,
              COUNT(*) FILTER (WHERE verdict IS NULL)    AS missed
            FROM anon_chats
            WHERE created_at > NOW() - INTERVAL '1 hour';
            """
        ) or []
    except Exception as e:
        return True, f"anon_chats unavailable; skipping ({e})", {}
    if not rows:
        return True, "no anon_chats rows yet; skipping", {}
    total = int(rows[0]["total"] or 0)
    missed = int(rows[0]["missed"] or 0)
    if total < 10:
        return True, f"too few samples ({total}); skipping", {"total": total}
    pct = round(100 * missed / total, 1)
    detail = {"total": total, "missed": missed, "pct": pct}
    if pct > miss_threshold_pct:
        return False, f"{pct}% of anon replies had no verdict in last hour", detail
    return True, f"{pct}% verdict-miss rate (ok, <{miss_threshold_pct}%)", detail


def check_reply_length_anomaly(q, ratio_threshold=3.0):
    """Avg reply length last hour vs preceding 24h. Spike => probably
    prompt regression or token leak."""
    try:
        rows = q(
            """
            SELECT
              COALESCE(AVG(LENGTH(reply)) FILTER
                 (WHERE created_at > NOW() - INTERVAL '1 hour'), 0) AS avg_1h,
              COALESCE(AVG(LENGTH(reply)) FILTER
                 (WHERE created_at BETWEEN NOW() - INTERVAL '25 hours'
                  AND NOW() - INTERVAL '1 hour'), 0) AS avg_24h
            FROM anon_chats;
            """
        ) or []
    except Exception as e:
        return True, f"anon_chats unavailable; skipping ({e})", {}
    if not rows or (rows[0]["avg_24h"] or 0) == 0:
        return True, "no 24h baseline yet; skipping", {}
    a1 = float(rows[0]["avg_1h"] or 0)
    a24 = float(rows[0]["avg_24h"] or 0)
    ratio = round(a1 / a24, 2) if a24 else 0
    detail = {"avg_1h": round(a1, 1), "avg_24h": round(a24, 1), "ratio": ratio}
    if ratio >= ratio_threshold:
        return False, f"avg reply length spiked {ratio}x (1h vs 24h baseline)", detail
    return True, f"reply length ratio {ratio}x (ok, <{ratio_threshold}x)", detail


def check_rate_limit_hotness(q, per_fp_threshold=20):
    try:
        rows = q(
            """
            SELECT fingerprint, COUNT(*) AS n
            FROM anon_chats
            WHERE created_at > NOW() - INTERVAL '1 hour'
            GROUP BY fingerprint
            HAVING COUNT(*) > %s
            ORDER BY n DESC
            LIMIT 5;
            """,
            (per_fp_threshold,),
        ) or []
    except Exception as e:
        return True, f"anon_chats unavailable; skipping ({e})", {}
    if rows:
        return False, f"{len(rows)} fingerprint(s) over {per_fp_threshold}/hour", {
            "top_fingerprints": [
                {"fp": r["fingerprint"], "count": int(r["n"])}
                for r in rows
            ],
        }
    return True, "no rate-limit pressure", {}


def check_fallback_events(q, threshold=10):
    try:
        rows = q(
            "SELECT COUNT(*) AS n FROM llm_fallback_events "
            "WHERE created_at > NOW() - INTERVAL '1 hour';"
        ) or []
    except Exception as e:
        return True, f"llm_fallback_events unavailable; skipping ({e})", {}
    n = int(rows[0]["n"] or 0) if rows else 0
    if n > threshold:
        return False, f"{n} LLM fallback events in the last hour (>{threshold})", {"count": n}
    return True, f"{n} fallback events (ok)", {"count": n}


# ---------------------------------------------------------------
# Runner
# ---------------------------------------------------------------
CHECKS = [
    ("llm_provider",        check_llm_provider),
    ("verdict_coverage",    check_verdict_coverage),
    ("reply_length",        check_reply_length_anomaly),
    ("rate_limit_hotness",  check_rate_limit_hotness),
    ("fallback_events",     check_fallback_events),
]


def run_checks(q=None):
    """Execute every check. Returns {name: {ok, msg, detail}}.

    `q` is required for the DB-backed checks; the LLM-ping check runs
    regardless. Every check is wrapped so a bad one can't cascade.
    """
    out = {}
    for name, fn in CHECKS:
        try:
            # All DB checks accept `q`; the provider check ignores it.
            ok, msg, detail = fn(q) if fn is not check_llm_provider else fn()
        except Exception as e:
            ok, msg, detail = False, f"check raised: {e}", {}
        out[name] = {"ok": ok, "msg": msg, "detail": detail}
    return out


# ---------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------
def _format_summary(results):
    bad = [(n, r) for n, r in results.items() if not r["ok"]]
    if not bad:
        return "crittr.ai alerts — all checks passing", False
    lines = [f"crittr.ai alerts — {len(bad)} check(s) failing"]
    for name, r in bad:
        lines.append(f"  • {name}: {r['msg']}")
    return "\n".join(lines), True


def _post_slack(webhook_url, text):
    if not webhook_url:
        return
    try:
        body = json.dumps({"text": text}).encode("utf-8")
        req = urllib_request.Request(
            webhook_url, data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib_request.urlopen(req, timeout=5) as resp:
            resp.read()
    except URLError as e:
        log.warning("slack alert POST failed: %s", e)


def _send_email(to_addr, text):
    if not to_addr:
        return
    try:
        # emails.py (if present) has a simple send_email helper.
        from emails import send_email
    except Exception:
        log.info("emails.py not available; skipping email alert")
        return
    try:
        send_email(
            to=to_addr,
            subject="crittr.ai alerts",
            body=text,
        )
    except Exception as e:
        log.warning("alert email send failed: %s", e)


def notify(summary_text, has_failures):
    """Route a summary to the configured channels. Always prints."""
    print(summary_text)
    if not has_failures:
        return
    _post_slack(os.environ.get("SLACK_ALERT_WEBHOOK"), summary_text)
    _send_email(os.environ.get("ALERT_EMAIL"), summary_text)


# ---------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------
def _load_q_from_app():
    """Import app.py's query helper if present. Falls back to None."""
    try:
        from app import q  # type: ignore
        return q
    except Exception as e:
        log.warning("could not import q from app: %s", e)
        return None


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    q = _load_q_from_app()
    results = run_checks(q)
    summary, failed = _format_summary(results)
    print(json.dumps(results, indent=2, default=str))
    print()
    print(summary)
    if failed and not args.dry_run:
        notify(summary, True)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
