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

# TWO CEILINGS, BECAUSE A DAILY CAP ALONE LIES ABOUT THE BILL. A daily cap is the only
# thing that can stop a runaway in progress — a monthly cap would let a bad afternoon burn
# the whole month before anything noticed. But a daily cap is ALSO a monthly cap multiplied
# by thirty, and that is the number that actually shows up on a card. $15/day sounds modest
# and is $450/month. So: the daily cap stops the runaway, the monthly cap bounds the bill,
# and whichever is hit first wins.
#
# The defaults are sized off real cost, not off what sounds safe. A triage exchange on
# haiku is roughly 1,500 input + 300 output tokens ≈ $0.0033 — about a third of a cent.
# $2/day is ~600 messages a day; $25/month is ~7,500 messages a month. Both sit far above
# anything crittr sees today, and both are survivable if something goes wrong overnight.
DAILY_USD = float(os.environ.get("CRITTR_AI_DAILY_BUDGET_USD", "2"))
MONTHLY_USD = float(os.environ.get("CRITTR_AI_MONTHLY_BUDGET_USD", "25"))
# Warn once per day when spend crosses this fraction of either cap.
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


def spent_month_micros(q1):
    """Calendar month to date, UTC — the window a card statement is cut on."""
    row = q1("""SELECT COALESCE(SUM(cost_micros),0) AS c, COUNT(*) AS n
                FROM ai_usage
                WHERE day >= date_trunc('month', (NOW() AT TIME ZONE 'utc'))::date""")
    return int((row or {}).get("c") or 0), int((row or {}).get("n") or 0)


def status(q1):
    d_micros, d_calls = spent_today_micros(q1)
    m_micros, m_calls = spent_month_micros(q1)
    d_cap = int(DAILY_USD * 1_000_000)
    m_cap = int(MONTHLY_USD * 1_000_000)
    d_hit = DAILY_USD > 0 and d_micros >= d_cap
    m_hit = MONTHLY_USD > 0 and m_micros >= m_cap
    return {
        "day": str(_today()),
        "spent_usd": round(d_micros / 1_000_000, 4),
        "cap_usd": DAILY_USD,
        "used_pct": round(100.0 * d_micros / d_cap, 1) if d_cap else None,
        "calls": d_calls,
        "month_spent_usd": round(m_micros / 1_000_000, 4),
        "month_cap_usd": MONTHLY_USD,
        "month_used_pct": round(100.0 * m_micros / m_cap, 1) if m_cap else None,
        "month_calls": m_calls,
        # Kept for callers that only ever asked "can it still spend?"
        "exhausted": d_hit or m_hit,
        "exhausted_by": "month" if m_hit else ("day" if d_hit else None),
    }


def may_call(q1):
    """Checked before every model call. (allowed, reason).

    Either ceiling can stop it, and the message names which one — "come back tomorrow" is
    wrong and annoying if the real answer is "come back in September".
    """
    if MONTHLY_USD > 0:
        micros, _ = spent_month_micros(q1)
        if micros >= MONTHLY_USD * 1_000_000:
            return False, (f"crittr's AI budget for this month (${MONTHLY_USD:.2f}) is used "
                           f"up. It resets on the 1st.")
    if DAILY_USD > 0:
        micros, _ = spent_today_micros(q1)
        if micros >= DAILY_USD * 1_000_000:
            return False, (f"crittr's AI budget for today (${DAILY_USD:.2f}) is used up. "
                           f"It resets at midnight UTC.")
    return True, ""              # 0 or negative on both = no cap configured


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
    """One email per day when spend crosses the warning line on EITHER ceiling. The monthly
    line matters more: a month can creep past 75% over three quiet weeks without any single
    day ever looking unusual, which is exactly the drift a daily-only warning misses."""
    micros, calls = spent_today_micros(q1)
    m_micros, m_calls = spent_month_micros(q1)
    day_hot = DAILY_USD > 0 and micros >= DAILY_USD * 1_000_000 * WARN_AT
    month_hot = MONTHLY_USD > 0 and m_micros >= MONTHLY_USD * 1_000_000 * WARN_AT
    if not (day_hot or month_hot):
        return
    try:
        row = q1("SELECT 1 AS x FROM ai_budget_alerts WHERE day=(NOW() AT TIME ZONE 'utc')::date")
        if row:
            return
        q("""INSERT INTO ai_budget_alerts (day) VALUES ((NOW() AT TIME ZONE 'utc')::date)
             ON CONFLICT (day) DO NOTHING""", fetch=False)
    except Exception:
        return
    spent, m_spent = micros / 1_000_000, m_micros / 1_000_000
    which = "month" if month_hot else "day"
    log.error("[ai_budget] spend is at $%.2f/$%.2f today and $%.2f/$%.2f this month "
              "(%s calls today, %s this month) — %s ceiling is the hot one",
              spent, DAILY_USD, m_spent, MONTHLY_USD, calls, m_calls, which)
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
            "subject": (f"crittr AI spend at ${m_spent:.2f} this month"
                        if month_hot else f"crittr AI spend at ${spent:.2f} today"),
            "text": (f"Today:      ${spent:.2f} of ${DAILY_USD:.2f} across {calls} calls\n"
                     f"This month: ${m_spent:.2f} of ${MONTHLY_USD:.2f} across {m_calls} "
                     f"calls\n\n"
                     f"At either ceiling the assistant stops calling the model and tells "
                     f"visitors it is unavailable — it will not keep spending.\n\n"
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
        def _card(label, spent, cap, pct, calls, hit, note):
            colour = "#A32020" if hit else (
                "#B4541F" if (pct or 0) >= WARN_AT * 100 else "#3E6340")
            return (
                f"<div style='flex:1 1 260px;background:#fff;border:1px solid #DFE5DB;"
                f"border-radius:12px;padding:22px'>"
                f"<div style='color:#6E7D70;font-size:12px;letter-spacing:.06em;"
                f"text-transform:uppercase'>{label}</div>"
                f"<div style='font-size:38px;font-weight:800;color:{colour};margin:6px 0;"
                f"letter-spacing:-.02em;font-variant-numeric:tabular-nums'>${spent:,.2f}"
                f"<span style='font-size:18px;font-weight:400;color:#6E7D70'> of "
                f"${cap:,.2f}</span></div>"
                f"<div style='background:#EEF2ED;border-radius:99px;height:8px;"
                f"overflow:hidden'><div style='background:{colour};height:8px;"
                f"width:{min(100, pct or 0)}%'></div></div>"
                f"<div style='color:#6E7D70;font-size:14px;margin-top:10px'>{calls} calls"
                + (f"  ·  <strong style='color:#A32020'>{note}</strong>" if hit else "")
                + "</div></div>")

        cards = (
            "<div style='display:flex;flex-wrap:wrap;gap:16px;margin-bottom:22px'>"
            + _card("Today", st["spent_usd"], st["cap_usd"], st["used_pct"], st["calls"],
                    st["exhausted_by"] == "day", "capped until midnight UTC")
            + _card("This month", st["month_spent_usd"], st["month_cap_usd"],
                    st["month_used_pct"], st["month_calls"],
                    st["exhausted_by"] == "month", "capped until the 1st")
            + "</div>")
        return (
            "<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>AI spend · crittr</title></head>"
            "<body style=\"margin:0;background:#FDFBF5;color:#1C2A1F;font:16px/1.55 "
            "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif\">"
            "<div style='max-width:820px;margin:0 auto;padding:32px 20px 64px'>"
            "<h1 style='font-size:28px;margin:0 0 6px'>AI spend</h1>"
            "<p style='color:#6E7D70;margin:0 0 24px'>Every model call crittr makes, and "
            "what it cost. Whichever ceiling is reached first stops the spending — "
            "enforced, not advisory.</p>"
            + cards +
            "<h2 style='font-size:18px;margin:0 0 10px'>Last 14 days</h2>"
            "<table style='width:100%;border-collapse:collapse;font-size:14px'>"
            + (bars or "<tr><td style='padding:20px 0;color:#6E7D70'>No model calls "
                       "recorded yet.</td></tr>") + "</table>"
            f"<p style='color:#6E7D70;font-size:13px;margin-top:22px'>Caps set by "
            f"<code>CRITTR_AI_DAILY_BUDGET_USD</code> (${DAILY_USD:,.2f}) and "
            f"<code>CRITTR_AI_MONTHLY_BUDGET_USD</code> (${MONTHLY_USD:,.2f}). A typical "
            f"exchange costs about a third of a cent, so ${DAILY_USD:,.2f} is roughly "
            f"{int(DAILY_USD / 0.0033):,} messages a day. Costs are estimated from token "
            f"counts and rounded up — the provider's dashboard is the bill of record.</p>"
            "</div></body></html>")
