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
import calendar
import logging
from datetime import datetime, timedelta, timezone

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
    # There are two kinds of alert now and they must not silence each other: a 'pace' warning
    # on the 3rd would otherwise consume the day's slot and suppress the 'level' warning that
    # matters when the cap is genuinely close. Widen the key rather than add a second table.
    q("ALTER TABLE ai_budget_alerts ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'level'",
      fetch=False)
    q("ALTER TABLE ai_budget_alerts DROP CONSTRAINT IF EXISTS ai_budget_alerts_pkey",
      fetch=False)
    q("""CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_alert_day_kind
         ON ai_budget_alerts(day, kind)""", fetch=False)


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


# A pace alert needs enough signal to mean something. Two guards, because they fail
# differently: the elapsed floor stops a burst at 00:20 on the 1st from dividing by nearly
# zero and projecting a fortune, and the spend floor stops a few cents of genuine traffic
# from projecting a fortune honestly. Both must clear before crittr claims to know a rate.
MIN_ELAPSED_DAYS = 0.5
MIN_SPEND_FRACTION = 0.15
# Below this there is no story to tell — landing 1 day early is arithmetic noise, not news.
PACE_ALERT_DAYS_EARLY = 3


def projection(q1):
    """Where this month lands if the current rate holds — and what date the money runs out.

    This is the question a level alert cannot answer. 75% of the cap on the 28th is a
    perfectly normal month; 75% on the 3rd means it is gone by the 5th and then crittr is
    silent for twenty-six days. Same number, opposite meaning, and only the date separates
    them.
    """
    micros, calls = spent_month_micros(q1)
    now = datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    elapsed = (now - start).total_seconds() / 86400.0
    cap = MONTHLY_USD * 1_000_000
    spent = micros / 1_000_000

    out = {
        "spent_usd": round(spent, 4),
        "cap_usd": MONTHLY_USD,
        "calls": calls,
        "elapsed_days": round(elapsed, 2),
        "days_in_month": days_in_month,
        "per_day_usd": None, "projected_usd": None,
        "exhausts_on": None, "days_early": None, "overshooting": False,
        "confident": False,
    }
    if cap <= 0 or elapsed < MIN_ELAPSED_DAYS or micros < cap * MIN_SPEND_FRACTION:
        return out

    out["confident"] = True
    per_day = micros / elapsed
    out["per_day_usd"] = round(per_day / 1_000_000, 4)
    out["projected_usd"] = round(per_day * days_in_month / 1_000_000, 2)
    if per_day <= 0:
        return out

    # Day-of-month, as a fraction, at which cumulative spend would reach the cap.
    exhaust_at = cap / per_day
    if exhaust_at >= days_in_month:
        return out                      # lands inside the month's own budget — nothing to say
    out["overshooting"] = True
    out["exhausts_on"] = (start + timedelta(days=exhaust_at)).date().isoformat()
    out["days_early"] = round(days_in_month - exhaust_at, 1)
    return out


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


def _claim_alert_slot(q, q1, kind):
    """True exactly once per (day, kind). The DB is the lock, so two web workers racing on
    the same call cannot both send. Returns False on any DB trouble — a missed warning is
    better than a mail loop."""
    try:
        row = q1("""SELECT 1 AS x FROM ai_budget_alerts
                    WHERE day=(NOW() AT TIME ZONE 'utc')::date AND kind=%s""", (kind,))
        if row:
            return False
        q("""INSERT INTO ai_budget_alerts (day, kind)
             VALUES ((NOW() AT TIME ZONE 'utc')::date, %s)
             ON CONFLICT (day, kind) DO NOTHING""", (kind,), fetch=False)
        return True
    except Exception as e:                                  # noqa: BLE001
        log.warning("[ai_budget] could not claim alert slot %s: %s", kind, e)
        return False


def _send_alert(subject, body):
    log.error("[ai_budget] %s", subject)
    key = os.environ.get("RESEND_API_KEY", "")
    to = os.environ.get("ALERT_EMAIL") or os.environ.get("REPLY_TO_EMAIL")
    if not (key and to):
        log.warning("[ai_budget] no RESEND_API_KEY/ALERT_EMAIL — alert logged, not emailed")
        return
    try:
        import resend
        resend.api_key = key
        resend.Emails.send({
            "from": os.environ.get("FROM_EMAIL", "crittr <hello@crittr.ai>"),
            "to": [to],
            "subject": subject,
            "text": body + "\n\nDetail: https://crittr.ai/admin/ai-spend\n",
        })
    except Exception as e:                                  # noqa: BLE001
        log.warning("[ai_budget] alert email failed: %s", e)


def _maybe_warn(q, q1):
    """Two independent warnings, because they answer different questions.

    LEVEL — "the cap is close." Fires at 75% of either ceiling.
    PACE  — "the cap is close TOO EARLY." Fires when the current burn rate exhausts the
            month before the month ends. This is the one that gives you time to act: at 75%
            on the 3rd, a level alert tells you a number, and a pace alert tells you the
            money is gone on the 5th and crittr goes quiet for the next twenty-six days.

    Each kind gets its own once-per-day slot so neither suppresses the other.
    """
    micros, calls = spent_today_micros(q1)
    m_micros, m_calls = spent_month_micros(q1)
    spent, m_spent = micros / 1_000_000, m_micros / 1_000_000

    # ── level ────────────────────────────────────────────────────────────────
    day_hot = DAILY_USD > 0 and micros >= DAILY_USD * 1_000_000 * WARN_AT
    month_hot = MONTHLY_USD > 0 and m_micros >= MONTHLY_USD * 1_000_000 * WARN_AT
    if (day_hot or month_hot) and _claim_alert_slot(q, q1, "level"):
        _send_alert(
            (f"crittr AI spend at ${m_spent:.2f} this month" if month_hot
             else f"crittr AI spend at ${spent:.2f} today"),
            (f"Today:      ${spent:.2f} of ${DAILY_USD:.2f} across {calls} calls\n"
             f"This month: ${m_spent:.2f} of ${MONTHLY_USD:.2f} across {m_calls} calls\n\n"
             f"At either ceiling the assistant stops calling the model and tells visitors "
             f"it is unavailable — it will not keep spending."))

    # ── pace ─────────────────────────────────────────────────────────────────
    p = projection(q1)
    if not (p["overshooting"] and (p["days_early"] or 0) >= PACE_ALERT_DAYS_EARLY):
        return
    if not _claim_alert_slot(q, q1, "pace"):
        return
    _send_alert(
        f"crittr AI budget runs out {int(p['days_early'])} days early — on {p['exhausts_on']}",
        (f"crittr is spending faster than the month can afford.\n\n"
         f"  Spent so far   ${p['spent_usd']:.2f} of ${p['cap_usd']:.2f} "
         f"({p['calls']} calls in {p['elapsed_days']:.1f} days)\n"
         f"  Current rate   ${p['per_day_usd']:.2f}/day\n"
         f"  On track for   ${p['projected_usd']:.2f} this month\n"
         f"  Runs out       {p['exhausts_on']} — {p['days_early']:.0f} days before the "
         f"month resets\n\n"
         f"Nothing is broken and nothing has overspent: the ${p['cap_usd']:.2f} ceiling "
         f"still holds. But if this rate continues the assistant goes quiet on "
         f"{p['exhausts_on']} and stays quiet until the 1st.\n\n"
         f"Either raise CRITTR_AI_MONTHLY_BUDGET_USD, or find out what is driving the "
         f"traffic — /admin/ai-spend breaks today down by purpose."))


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
            "pace": projection(q1),
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

        # The pace line. Shown whenever there is enough signal to state a rate — not only
        # when it is bad news, because "on track for $6 of $25" is the reassurance that
        # makes the warning worth believing when it does appear.
        p = projection(q1)
        if not p["confident"]:
            pace = (f"<div style='color:#6E7D70;font-size:14px;margin:-6px 0 24px'>"
                    f"Not enough spend yet this month to project a rate.</div>")
        elif p["overshooting"]:
            pace = (
                f"<div style='background:#FBF1E8;border:1px solid #E8CDB2;border-left:3px "
                f"solid #B4541F;border-radius:10px;padding:16px 18px;margin:-6px 0 24px'>"
                f"<strong style='color:#B4541F'>Running out {p['days_early']:.0f} days "
                f"early.</strong> At ${p['per_day_usd']:.2f}/day this month is on track for "
                f"<strong>${p['projected_usd']:,.2f}</strong>, so the ${p['cap_usd']:,.2f} "
                f"ceiling is reached on <strong>{p['exhausts_on']}</strong> — after which "
                f"the assistant is unavailable until the 1st.</div>")
        else:
            pace = (
                f"<div style='color:#6E7D70;font-size:14px;margin:-6px 0 24px'>"
                f"At ${p['per_day_usd']:.2f}/day, on track for "
                f"<strong style='color:#3E6340'>${p['projected_usd']:,.2f}</strong> "
                f"this month — inside the ${p['cap_usd']:,.2f} ceiling.</div>")
        cards += pace
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
