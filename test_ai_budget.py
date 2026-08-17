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

print("\n== the warning fires once, not once per call ==")
q("DELETE FROM ai_usage", fetch=False)
q("DELETE FROM ai_budget_alerts", fetch=False)
for _ in range(5):
    spend(0.35)
    B._maybe_warn(q, q1)
check("exactly one alert row for today", len(q("SELECT * FROM ai_budget_alerts")), 1)

print("\n== estimated cost is in the right order of magnitude ==")
c = B.cost_micros("claude-haiku-4-5-20251001", 1500, 300)
print(f"  a 1500-in/300-out haiku exchange = ${c/1_000_000:.4f}")
check("a typical exchange is under a cent", c < 10_000, True)

print("\n" + ("ALL PASS" if not fails else f"FAILURES: {fails}"))
raise SystemExit(1 if fails else 0)
