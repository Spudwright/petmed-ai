"""Tests for the membership revenue share — no Stripe, no database.

The failure that matters here is not an exception, it is a WRONG NUMBER that nobody
notices until a veterinarian reconciles a statement and finds crittr paid them for a month
the owner got refunded. So these test the arithmetic and the refusals:

  1. the share is taken from what Stripe COLLECTED, never from list price
  2. a member with no claimed practice credits nobody, silently
  3. a refund is a NEGATIVE row, never an edit to the original
  4. a repeated refund webhook does not reverse twice
  5. a covered consult never reaches Stripe checkout
"""
import os
import sys

import member_plan as mp

FAIL = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}"
          f"{(' — ' + str(detail)[:120]) if detail else ''}")
    if not cond:
        FAIL.append(label)


class DB:
    """A practice link and an existing credit, each absent unless a test supplies it."""

    def __init__(self, *, practice=None, credit=None, reversed_cents=0):
        self.practice = practice
        self.credit = credit
        self.reversed_cents = reversed_cents
        self.writes = []

    def q(self, sql, params=None, fetch=True):
        self.writes.append((" ".join(sql.split()), params))
        return []

    def q1(self, sql, params=None):
        s = " ".join(sql.split())
        if "FROM practice_clients" in s:
            return self.practice
        if "share_cents < 0" in s:
            return {"s": self.reversed_cents}
        if "FROM plan_attributions" in s:
            return self.credit
        return None

    def inserts(self):
        return [(s, p) for s, p in self.writes if s.startswith("INSERT INTO plan_attributions")]


PRACTICE = {"id": 7, "name": "Sunland Animal Hospital"}


def main():
    print("== the share comes off what was COLLECTED ==")
    d = DB(practice=PRACTICE)
    # $19.99 list, but a coupon meant Stripe only took $14.99.
    share = mp.credit_subscription(d.q, d.q1, user_id=42,
                                   invoice_id="in_1", amount_cents=1499)
    expected = int(round(1499 * mp.MEMBER_REV_SHARE_PCT / 100.0))
    check(f"{mp.MEMBER_REV_SHARE_PCT}% of $14.99 collected = {expected}c, not of $19.99 list",
          share == expected, share)
    row = d.inserts()[0][1]
    check("the rate is frozen onto the row", mp.MEMBER_REV_SHARE_PCT in row, row)
    check("amount recorded is what was collected", 1499 in row, row)

    print("\n== a member nobody introduced credits nobody ==")
    d = DB(practice=None)
    share = mp.credit_subscription(d.q, d.q1, user_id=42,
                                   invoice_id="in_2", amount_cents=1999)
    check("no claimed practice, no credit", share is None, share)
    check("and nothing was written", not d.inserts(), d.inserts())

    print("\n== zero and negative invoices are refused ==")
    d = DB(practice=PRACTICE)
    check("a $0 invoice credits nothing",
          mp.credit_subscription(d.q, d.q1, user_id=42, invoice_id="in_3",
                                 amount_cents=0) is None)
    check("a missing invoice id credits nothing",
          mp.credit_subscription(d.q, d.q1, user_id=42, invoice_id=None,
                                 amount_cents=1999) is None)

    print("\n== a refund is a negative row, not an edit ==")
    orig = {"id": 1, "practice_id": 7, "vet_id": 3, "owner_user_id": 42,
            "amount_cents": 1999, "share_pct": 50, "share_cents": 1000}
    d = DB(practice=PRACTICE, credit=orig)
    back = mp.reverse_subscription(d.q, d.q1, invoice_id="in_1")
    check("reversal equals the original, negated", back == -1000, back)
    ins = d.inserts()
    check("it INSERTs rather than UPDATEs", len(ins) == 1 and "INSERT" in ins[0][0])
    check("no UPDATE touched the original credit",
          not any(s.startswith("UPDATE plan_attributions") for s, _ in d.writes))
    check("the negative share is on the row", -1000 in ins[0][1], ins[0][1])

    print("\n== Stripe retries the refund webhook ==")
    d = DB(practice=PRACTICE, credit=orig, reversed_cents=-1000)
    again = mp.reverse_subscription(d.q, d.q1, invoice_id="in_1")
    check("an already-reversed invoice does not reverse twice", again is None, again)
    check("and wrote nothing", not d.inserts())

    print("\n== a covered consult cannot reach Stripe ==")
    import inspect
    import consults
    src = inspect.getsource(consults.checkout_url)
    check("checkout_url refuses a covered consult", 'c.get("covered")' in src)
    qsrc = inspect.getsource(consults.quote)
    check("a covered consult is booked at zero", "0,0,0,0,'paid',TRUE" in qsrc)
    covered_branch = qsrc.split("if covered:")[1].split("amount = int(amount_cents")[0]
    check("no platform fee is split inside the covered branch",
          "_split(" not in covered_branch)
    check("the covered branch really is the code under test",
          "INSERT INTO consults" in covered_branch and "covered, paid_at" in covered_branch)

    print("\n" + ("ALL PASS" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}"))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
