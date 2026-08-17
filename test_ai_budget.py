"""Both ceilings, against a real Postgres.

The one that matters here is the MONTHLY cap firing while the DAY is still cheap — that is
the case a daily-only governor gets wrong, and it is the case that produces a surprising
card statement: thirty unremarkable days that add up to a number nobody agreed to.
"""
import os

os.environ["CRITTR_AI_DAILY_BUDGET_USD"] = "1.00"
os.environ["CRITTR_AI_MONTHLY_BUDGET_USD"] = "2.00"
os.environ.pop("RESEND_API_KEY", None)          # no live email from a test

import pgserver, tempfile, pathlib

_d = pathlib.Path(tempfile.mkdtemp()) / "pg"
_d.mkdir(parents=True, exist_ok=True)
_srv = pgserver.get_server(_d)
os.environ["DATABASE_URL"] = _srv.get_uri()

import psycopg2
from psycopg2.extras import RealDictCursor

_conn = psycopg2.connect(os.environ["DATABASE_URL"], cursor_factory=RealDictCursor)
_conn.autocommit = True


def q(sql, params=None, fetch=True):
    with _conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchall() if fetch else None


def q1(sql, params=None):
    rows = q(sql, params)
    return rows[0] if rows else None


import ai_budget as B

B.init_budget_tables(q)
fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: {got}" + ("" if ok else f"  (want {want})"))
    if not ok:
        fails.append(label)


def spend(usd, days_ago=0):
    """Book a known cost directly, so the assertions do not depend on the price table."""
    q("""INSERT INTO ai_usage (day, provider, model, purpose, cost_micros)
         VALUES ((NOW() AT TIME ZONE 'utc')::date - %s, 'test', 'test', 'triage', %s)""",
      (days_ago, int(usd * 1_000_000)), fetch=False)


print("\n== the day ceiling stops a runaway in progress ==")
check("fresh budget allows calls", B.may_call(q1)[0], True)
spend(0.99)
check("just under the day cap still allows", B.may_call(q1)[0], True)
spend(0.02)
ok, why = B.may_call(q1)
check("over the day cap refuses", ok, False)
check("names the daily reset", "midnight UTC" in why, True)
check("status attributes it to the day", B.status(q1)["exhausted_by"], "day")

print("\n== the month ceiling stops the slow creep a daily cap never sees ==")
q("DELETE FROM ai_usage", fetch=False)
# Six days at 30c. No single day is anywhere near the $1 daily cap...
for d in range(1, 7):
    spend(0.30, days_ago=d)
check("today is clean", B.status(q1)["spent_usd"], 0.0)
check("day cap is untouched", B.status(q1)["used_pct"], 0.0)
# ...but $1.80 of a $2 month is spent, and one more ordinary day tips it over.
check("month is at 90%", B.status(q1)["month_used_pct"], 90.0)
check("still allowed at 90%", B.may_call(q1)[0], True)
spend(0.25)
ok, why = B.may_call(q1)
check("month cap refuses despite a cheap day", ok, False)
check("names the monthly reset, not tomorrow", "resets on the 1st" in why, True)
check("does NOT tell them to come back tomorrow", "midnight" in why, False)
check("status attributes it to the month", B.status(q1)["exhausted_by"], "month")
check("the day itself is still under its own cap",
      B.status(q1)["spent_usd"] < B.DAILY_USD, True)

print("\n== last month's spending does not count against this month ==")
q("DELETE FROM ai_usage", fetch=False)
q("""INSERT INTO ai_usage (day, provider, model, purpose, cost_micros)
     VALUES (date_trunc('month',(NOW() AT TIME ZONE 'utc'))::date - 1,
             'test','test','triage', 99000000)""", fetch=False)
check("prior month excluded from the month total", B.status(q1)["month_spent_usd"], 0.0)
check("and does not block calls", B.may_call(q1)[0], True)

print("\n== a cap of 0 means no cap ==")
q("DELETE FROM ai_usage", fetch=False)
spend(500.0)
B.DAILY_USD, B.MONTHLY_USD = 0, 0
check("uncapped allows through", B.may_call(q1)[0], True)
B.DAILY_USD, B.MONTHLY_USD = 1.0, 2.0

print("\n== the warning fires once per kind, not once per call ==")
q("DELETE FROM ai_usage", fetch=False)
q("DELETE FROM ai_budget_alerts", fetch=False)
for _ in range(5):
    spend(0.35)
    B._maybe_warn(q, q1)
rows = q("SELECT kind FROM ai_budget_alerts")
check("exactly one 'level' alert despite 5 calls",
      len([r for r in rows if r["kind"] == "level"]), 1)
check("no kind is sent twice", len(rows), len({r["kind"] for r in rows}))

print("\n== burn rate: the cap is close TOO EARLY ==")
# Rebuild the month so the arithmetic is exact regardless of today's date.
import datetime as _dt

B.MONTHLY_USD = 25.0
# Lift the DAY cap out of the way: this section is about the month's pace, and the $1 daily
# cap used above would otherwise be what blocks the call, proving nothing about the month.
# Production has the same shape ($2/day × 31 = $62 against a $25 month), so the month is
# the binding ceiling there too.
B.DAILY_USD = 100.0
now = _dt.datetime.now(_dt.timezone.utc)
elapsed_days = (now - now.replace(day=1, hour=0, minute=0, second=0,
                                  microsecond=0)).total_seconds() / 86400.0
import calendar as _cal
dim = _cal.monthrange(now.year, now.month)[1]

q("DELETE FROM ai_usage", fetch=False)
q("DELETE FROM ai_budget_alerts", fetch=False)
if elapsed_days < B.MIN_ELAPSED_DAYS or elapsed_days / dim > 0.85:
    print(f"  SKIP  {elapsed_days:.1f}d into a {dim}d month — no room to sit between "
          f"'pacing badly' and 'already blocked'")
else:
    # THE CASE THAT MATTERS: overspending the RATE while still under the CAP. That gap is
    # the whole point — it is the only window in which a warning can still change anything.
    # Anything at or above the cap is just the governor, and by then it is too late to act.
    # Must satisfy cap*(elapsed/days) < spend < cap; the midpoint of that range always does.
    spend(25.0 * (elapsed_days / dim + 1) / 2)
    p = B.projection(q1)
    print(f"  ${p['spent_usd']:.2f} in {p['elapsed_days']:.1f}d "
          f"= ${p['per_day_usd']:.2f}/day -> ${p['projected_usd']:.2f}/month, "
          f"out on {p['exhausts_on']} ({p['days_early']:.0f}d early)")
    check("rate is confident", p["confident"], True)
    check("flags the overshoot", p["overshooting"], True)
    check("projects over the cap", p["projected_usd"] > 25.0, True)
    check("names an exhaustion date", bool(p["exhausts_on"]), True)
    check("early enough to be worth an alert",
          p["days_early"] >= B.PACE_ALERT_DAYS_EARLY, True)
    check("exhaustion date is in the future", p["exhausts_on"] > str(B._today()), True)
    check("but nothing is blocked yet — there is still time to act", B.may_call(q1)[0], True)

    print("\n  -- and the alert is a PACE alert, distinct from the level one --")
    B._maybe_warn(q, q1)
    kinds = sorted(r["kind"] for r in q("SELECT kind FROM ai_budget_alerts"))
    check("pace alert claimed its own slot", "pace" in kinds, True)
    B._maybe_warn(q, q1)
    check("does not repeat within the day", len(q("SELECT * FROM ai_budget_alerts")), len(kinds))

print("\n== burn rate stays QUIET when spending is affordable ==")
q("DELETE FROM ai_usage", fetch=False)
q("DELETE FROM ai_budget_alerts", fetch=False)
if elapsed_days >= B.MIN_ELAPSED_DAYS:
    spend(0.5 * (25.0 / dim) * elapsed_days)     # half the affordable rate
    p = B.projection(q1)
    if p["confident"]:
        check("no overshoot flagged", p["overshooting"], False)
        check("projects under the cap", p["projected_usd"] < 25.0, True)
    B._maybe_warn(q, q1)
    check("no pace alert sent",
          [r["kind"] for r in q("SELECT kind FROM ai_budget_alerts") if r["kind"] == "pace"],
          [])

print("\n== a burst on day 1 does not project a fortune ==")
q("DELETE FROM ai_usage", fetch=False)
q("DELETE FROM ai_budget_alerts", fetch=False)
_saved = B.MIN_ELAPSED_DAYS
B.MIN_ELAPSED_DAYS = 999                        # simulate "barely into the month"
spend(20.0)
p = B.projection(q1)
check("refuses to state a rate too early", p["confident"], False)
check("and claims no exhaustion date", p["exhausts_on"], None)
B._maybe_warn(q, q1)
check("no pace alert from a too-early burst",
      [r["kind"] for r in q("SELECT kind FROM ai_budget_alerts") if r["kind"] == "pace"], [])
check("but the LEVEL alert still fires — $20 of $25 is real",
      [r["kind"] for r in q("SELECT kind FROM ai_budget_alerts") if r["kind"] == "level"],
      ["level"])
B.MIN_ELAPSED_DAYS = _saved

print("\n== a trickle does not project a fortune either ==")
q("DELETE FROM ai_usage", fetch=False)
q("DELETE FROM ai_budget_alerts", fetch=False)
spend(0.02)                                     # real, but far below the signal floor
check("refuses to state a rate from pennies", B.projection(q1)["confident"], False)

print("\n== the migration runs against the OLD table shape already in production ==")
# Production has ai_budget_alerts(day PRIMARY KEY) with no 'kind'. Rebuild exactly that,
# with a row in it, and prove the upgrade is non-destructive and repeatable — boot runs
# init_budget_tables every time, so it has to be safe on the hundredth run too.
q("DROP TABLE IF EXISTS ai_budget_alerts", fetch=False)
q("""CREATE TABLE ai_budget_alerts (day DATE PRIMARY KEY,
                                    sent_at TIMESTAMPTZ DEFAULT NOW())""", fetch=False)
q("INSERT INTO ai_budget_alerts (day) VALUES ((NOW() AT TIME ZONE 'utc')::date - 5)",
  fetch=False)
B.init_budget_tables(q)
check("existing alert row survived", len(q("SELECT * FROM ai_budget_alerts")), 1)
check("back-filled to the 'level' kind",
      q("SELECT kind FROM ai_budget_alerts")[0]["kind"], "level")
B.init_budget_tables(q)
B.init_budget_tables(q)
check("re-running the migration is a no-op", len(q("SELECT * FROM ai_budget_alerts")), 1)
q("DELETE FROM ai_budget_alerts", fetch=False)
q("DELETE FROM ai_usage", fetch=False)
B.DAILY_USD, B.MONTHLY_USD = 1.0, 2.0
spend(0.8)
B._maybe_warn(q, q1)
B._maybe_warn(q, q1)
check("and alerting still works after the upgrade",
      len(q("SELECT * FROM ai_budget_alerts")), 1)

print("\n== estimated cost is in the right order of magnitude ==")
c = B.cost_micros("claude-haiku-4-5-20251001", 1500, 300)
print(f"  a 1500-in/300-out haiku exchange = ${c/1_000_000:.4f}")
check("a typical exchange is under a cent", c < 10_000, True)

print("\n" + ("ALL PASS" if not fails else f"FAILURES: {fails}"))
raise SystemExit(1 if fails else 0)
