"""crittr.ai — a hard ceiling on what the AI can cost, and a record of what it did cost.

THE HOLE THIS CLOSES. rate_limiting caps a single visitor at 200 chats a day. It says
nothing about the total. A thousand visitors inside their limits is two hundred thousand
model calls, and the first anyone would know is the invoice. Worse, the bot gate is
currently failing open, so the endpoint is genuinely reachable by anything with a script.

Per-IP limits protect against one abuser. This protects against the bill.

HOW IT BEHAVES WHEN THE MONEY RUNS OUT. It stops calling the model and says so plainly to
the visitor. It does not fail silently, and it does not keep spending. A day where crittr
tells a hundred people "the assistant is resting" is recoverable; a day where it quietly
spends $4,000 is not.

WHY THE NUMBERS ARE ESTIMATES AND THAT IS FINE. Cost is computed from token counts and a
local price table, so it drifts when a provider changes pricing. It is a spend GOVERNOR,
not an accounting system — being roughly right and enforced beats being exact and advisory.
Bill of record is always the provider's own dashboard.
"""
import os
import json
import logging
from datetime import datetime, timezone

from flask import jsonify

log = logging.getLogger("crittr.ai_budget")

# What crittr is willing to spend on model calls in a single day, across every visitor.
DAILY_USD = float(os.environ.get("CRITTR_AI_DAILY_BUDGET_USD", "15"))
# Warn once per day when spend crosses this fraction of the cap.
WARN_AT = float(os.environ.get("CRITTR_AI_WARN_AT", "0.75"))

# USD per 1M tokens (input, output). Rough, and deliberately rounded UP so the governor
# errs toward stopping early rather than overspending.
PRICES = {
    "gpt-4o-mini":                (0.20, 0.70),
    "gpt-4o":                     (3.00, 12.00),
    "claude-haiku-4-5-20251001":  (1.10, 5.50),
    "_default":                   (1.00, 5.00),
}


def init_budget_tables(q):
    q("""
    CREATE TABLE IF NOT EXISTS ai_usage (
        id              SERIAL PRIMARY KEY,
        day             DATE NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc')::date,
        provider        TEXT,
        model           TEXT,
        purpose         TEXT,          -- 'triage' | 'chart' | 'screen' | ...
        prompt_tokens   INTEGER DEFAULT 0,
        output_tokens   INTEGER DEFAULT 0,
        -- Micro-dollars: integer arithmetic, no float drift in the running total.
        cost_micros     BIGINT DEFAULT 0,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""", fetch=False)
    q("CREATE INDEX IF NOT EXISTS idx_ai_usage_day ON ai_usage(day)", fetch=False)
    q("""
    CREATE TABLE IF NOT EXISTS ai_budget_alerts (
        day     DATE PRIMARY KEY,
        sent_at TIMESTAMPTZ DEFAULT NOW()
    )""", fetch=False)


def _today():
    return datetime.now(timezone.utc).date()


def cost_micros(model, prompt_tokens, output_tokens):
    pin, pout = PRICES.get(model or "", PRICES["_default"])
    return int(round((prompt_tokens or 0) * pin + (output_tokens or 0) * pout))


def spent_today_micros(q1):
    row = q1("""SELECT COALESCE(SUM(cost_micros),0) AS c, COUNT(*) AS n
                FROM ai_usage WHERE day = (NOW() AT TIME ZONE 'utc')::date""")
    return int((row or {}).get("c") or 0), int((row or {}).get("n") or 0)


def status(q1):
    micros, calls = spent_today_micros(q1)
    cap = int(DAILY_USD * 1_000_000)
    return {
        "day": str(_today()),
        "spent_usd": round(micros / 1_000_000, 4),
        "cap_usd": DAILY_USD,
        "used_pct": round(100.0 * micros / cap, 1) if cap else None,
        "calls": calls,
        "exhausted": micros >= cap,
    }


def may_call(q1):
    """Checked before every model call. (allowed, reason)."""
    if DAILY_USD <= 0:
        return True, ""          # 0 or negative = no cap configured
    micros, _ = spent_today_micros(q1)
    if micros >= DAILY_USD * 1_000_000:
        return False, (f"crittr's AI budget for today (${DAILY_USD:.2f}) is used up. "
                       f"It resets at midnight UTC.")
    return True, ""


def record(q, q1, *, provider, model, purpose, prompt_tokens=0, output_tokens=0):
    """Log one model call and its estimated cost. Never raises — a failure to record
    must not break a reply the visitor is already waiting for."""
    try:
        micros = cost_micros(model, prompt_tokens, output_tokens)
        q("""INSERT INTO ai_usage (provider, model, purpose, prompt_tokens,
                                   output_tokens, cost_micros)
             VALUES (%s,%s,%s,%s,%s,%s)""",
          (provider, model, purpose, prompt_tokens, output_tokens, micros), fetch=False)
        _maybe_warn(q, q1)
        return micros
    except Exception as e:                                  # noqa: BLE001
        log.warning("[ai_budget] could not record usage: %s", e)
        return 0


def _maybe_warn(q, q1):
    """One email per day when spend crosses the warning line. Once, not per call."""
    if DAILY_USD <= 0:
        return
    micros, calls = spent_today_micros(q1)
    if micros < DAILY_USD * 1_000_000 * WARN_AT:
        return
    try:
        row = q1("SELECT 1 AS x FROM ai_budget_alerts WHERE day=(NOW() AT TIME ZONE 'utc')::date")
        if row:
            return
        q("""INSERT INTO ai_budget_alerts (day) VALUES ((NOW() AT TIME ZONE 'utc')::date)
             ON CONFLICT (day) DO NOTHING""", fetch=False)
    except Exception:
        return
    spent = micros / 1_000_000
    log.error("[ai_budget] spend is at $%.2f of $%.2f today across %s calls",
              spent, DAILY_USD, calls)
    key = os.environ.get("RESEND_API_KEY", "")
    to = os.environ.get("ALERT_EMAIL") or os.environ.get("REPLY_TO_EMAIL")
    if not (key and to):
        return
    try:
        import resend
        resend.api_key = key
        resend.Emails.send({
            "from": os.environ.get("FROM_EMAIL", "crittr <hello@crittr.ai>"),
            "to": [to],
            "subject": f"crittr AI spend at ${spent:.2f} today",
            "text": (f"crittr has spent about ${spent:.2f} of its ${DAILY_USD:.2f} daily "
                     f"AI budget across {calls} calls.\n\n"
                     f"At the cap the assistant stops calling the model and tells visitors "
                     f"it is unavailable — it will not keep spending.\n\n"
                     f"Detail: https://crittr.ai/admin/ai-spend\n"),
        })
    except Exception as e:                                  # noqa: BLE001
        log.warning("[ai_budget] alert email failed: %s", e)


def register_budget_routes(app, q, q1, admin_required):

    @app.route("/api/admin/ai-spend", methods=["GET"])
    @admin_required
    def api_ai_spend():
        days = q("""SELECT day, COUNT(*) AS calls, SUM(cost_micros) AS micros
                    FROM ai_usage WHERE day > (NOW() AT TIME ZONE 'utc')::date - 14
                    GROUP BY day ORDER BY day DESC""") or []
        by_purpose = q("""SELECT purpose, COUNT(*) AS calls, SUM(cost_micros) AS micros
                          FROM ai_usage WHERE day = (NOW() AT TIME ZONE 'utc')::date
                          GROUP BY purpose ORDER BY micros DESC""") or []
        return jsonify({
            "today": status(q1),
            "last_14_days": [{"day": str(r["day"]), "calls": int(r["calls"]),
                              "usd": round(int(r["micros"] or 0) / 1_000_000, 4)}
                             for r in days],
            "today_by_purpose": [{"purpose": r["purpose"] or "?", "calls": int(r["calls"]),
                                  "usd": round(int(r["micros"] or 0) / 1_000_000, 4)}
                                 for r in by_purpose],
        })

    @app.route("/admin/ai-spend", methods=["GET"])
    @admin_required
    def ai_spend_page():
        st = status(q1)
        days = q("""SELECT day, COUNT(*) AS calls, SUM(cost_micros) AS micros
                    FROM ai_usage WHERE day > (NOW() AT TIME ZONE 'utc')::date - 14
                    GROUP BY day ORDER BY day DESC""") or []
        peak = max([int(r["micros"] or 0) for r in days] or [1]) or 1
        bars = ""
        for r in days:
            m = int(r["micros"] or 0)
            w = max(2, int(100 * m / peak))
            bars += (f"<tr><td style='padding:7px 12px 7px 0;white-space:nowrap;"
                     f"font-variant-numeric:tabular-nums'>{r['day']}</td>"
                     f"<td style='padding:7px 0;width:100%'>"
                     f"<div style='background:#527E54;height:16px;width:{w}%;"
                     f"border-radius:3px;min-width:2px'></div></td>"
                     f"<td style='padding:7px 0 7px 12px;text-align:right;white-space:nowrap;"
                     f"font-variant-numeric:tabular-nums'>${m/1_000_000:,.2f}</td>"
                     f"<td style='padding:7px 0 7px 14px;text-align:right;color:#6E7D70;"
                     f"font-variant-numeric:tabular-nums'>{int(r['calls'])}</td></tr>")
        colour = "#A32020" if st["exhausted"] else (
            "#B4541F" if (st["used_pct"] or 0) >= WARN_AT * 100 else "#3E6340")
        return (
            "<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>AI spend · crittr</title></head>"
            "<body style=\"margin:0;background:#FDFBF5;color:#1C2A1F;font:16px/1.55 "
            "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif\">"
            "<div style='max-width:820px;margin:0 auto;padding:32px 20px 64px'>"
            "<h1 style='font-size:28px;margin:0 0 6px'>AI spend</h1>"
            "<p style='color:#6E7D70;margin:0 0 24px'>Every model call crittr makes, and "
            "what it cost. The cap is enforced, not advisory.</p>"
            f"<div style='background:#fff;border:1px solid #DFE5DB;border-radius:12px;"
            f"padding:22px;margin-bottom:22px'>"
            f"<div style='color:#6E7D70;font-size:12px;letter-spacing:.06em;"
            f"text-transform:uppercase'>Today</div>"
            f"<div style='font-size:38px;font-weight:800;color:{colour};margin:6px 0;"
            f"letter-spacing:-.02em'>${st['spent_usd']:,.2f}"
            f"<span style='font-size:18px;font-weight:400;color:#6E7D70'> of "
            f"${st['cap_usd']:,.2f}</span></div>"
            f"<div style='background:#EEF2ED;border-radius:99px;height:8px;overflow:hidden'>"
            f"<div style='background:{colour};height:8px;"
            f"width:{min(100, st['used_pct'] or 0)}%'></div></div>"
            f"<div style='color:#6E7D70;font-size:14px;margin-top:10px'>"
            f"{st['calls']} calls today"
            + ("  ·  <strong style='color:#A32020'>cap reached — the assistant is telling "
               "visitors it is unavailable rather than spending more</strong>"
               if st["exhausted"] else "") +
            "</div></div>"
            "<h2 style='font-size:18px;margin:0 0 10px'>Last 14 days</h2>"
            "<table style='width:100%;border-collapse:collapse;font-size:14px'>"
            + (bars or "<tr><td style='padding:20px 0;color:#6E7D70'>No model calls "
                       "recorded yet.</td></tr>") + "</table>"
            f"<p style='color:#6E7D70;font-size:13px;margin-top:22px'>Cap set by "
            f"<code>CRITTR_AI_DAILY_BUDGET_USD</code> (currently ${DAILY_USD:,.2f}). "
            f"Costs are estimated from token counts — the provider's dashboard is the bill "
            f"of record.</p>"
            "</div></body></html>")
