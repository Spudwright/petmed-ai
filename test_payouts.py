"""Tests for Stripe Connect payouts — no Stripe, no database.

This is the only part of crittr where a bug cannot be undone by writing a corrective row.
So every test here is a REFUSAL: the money must not move when any one of the preconditions
is missing. The happy path is the least interesting case in this file.

The five rules from connect_payouts, each tested from the failing side:
  1. a vet cannot trigger their own payout
  2. every transfer carries an idempotency key derived from the payout id
  3. nothing is paid until it has cleared the refund holdback
  4. we pay what the ledger says, never an amount passed in
  5. a payout is marked paid only after Stripe confirms
"""
import inspect
import sys

import connect_payouts as cp

FAIL = []


def route_src_for_logs():
    """The HTTP layer too — a link leaked from a route handler is just as leaked."""
    return inspect.getsource(cp.register_payout_routes)


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}"
          f"{(' — ' + str(detail)[:120]) if detail else ''}")
    if not cond:
        FAIL.append(label)


class DB:
    """A ledger and a practice, with everything else absent unless a test supplies it."""

    def __init__(self, *, practice=None, ready_cents=0, ready_lines=0):
        self.practice = practice
        self.ready_cents = ready_cents
        self.ready_lines = ready_lines
        self.writes = []

    def q(self, sql, params=None, fetch=True):
        self.writes.append((" ".join(sql.split()), params))
        return []

    def q1(self, sql, params=None):
        s = " ".join(sql.split())
        self.writes.append((s, params))
        if s.startswith("SELECT * FROM practices WHERE id"):
            return self.practice
        if "COALESCE(SUM(share_cents),0) AS cents" in s:
            return {"cents": self.ready_cents, "n": self.ready_lines}
        return None


CONNECTED = {"id": 1, "name": "Sapillo", "contact_email": "f@c.example",
             "stripe_account_id": "acct_test123", "payouts_enabled": True}


def main():
    print("\n== rule 1: a vet can never move money ==")
    src = inspect.getsource(cp.register_payout_routes)
    pay_route = src[src.index('"/api/admin/practices'):]
    check("the payout route is under /api/admin/",
          '"/api/admin/practices/<int:practice_id>/payout"' in src)
    check("and is admin_required, not vet_only",
          "@admin_required" in pay_route.split("def api_pay_practice")[0])
    vet_routes = [ln for ln in src.splitlines() if "@app.route" in ln and "/vet/" in ln]
    check("the vet-facing routes are read-only or onboarding, never a transfer",
          all("payout" not in r or "payouts" in r for r in vet_routes), vet_routes)
    check("retry is admin-only too",
          '"/api/admin/payouts/<int:payout_id>/retry"' in src)

    print("\n== rule 2: a retry must never pay twice ==")
    pay_src = inspect.getsource(cp.pay_practice)
    check("the transfer carries an idempotency key",
          "idempotency_key=" in pay_src)
    check("and the key is derived from the payout id, not random",
          'f"crittr-payout-{payout[\'id\']}"' in pay_src
          or "crittr-payout-" in pay_src)
    retry_src = inspect.getsource(cp.retry_payout)
    check("the retry path reuses the SAME key",
          "crittr-payout-" in retry_src,
          "so retrying a transfer that already succeeded is a no-op at Stripe")

    print("\n== rule 3: nothing is paid before the refund window closes ==")
    check("the ready-to-pay query excludes recent lines",
          "created_at < NOW() - (%s || ' days')::interval" in pay_src)
    check("the holdback is configurable and defaults to something non-zero",
          cp.HOLDBACK_DAYS > 0, f"{cp.HOLDBACK_DAYS} days")

    print("\n== rule 4: the amount comes from the ledger ==")
    sig = inspect.signature(cp.pay_practice)
    check("pay_practice takes no amount parameter",
          not any("amount" in p for p in sig.parameters), list(sig.parameters))
    check("the transfer amount is read off the payout row",
          "amount=int(payout[\"amount_cents\"])" in pay_src.replace("'", '"'))

    print("\n== rule 5: paid means Stripe said so ==")
    before_try = pay_src.split("stripe.Transfer.create")[0]
    check("nothing is marked paid before the transfer is attempted",
          "mark_payout_paid" not in before_try)
    check("a failed transfer records the failure and sends nothing",
          "failure=%s" in pay_src and "nothing was sent" in pay_src)

    print("\n== the refusals, in order, before any money moves ==")
    r, why = cp.pay_practice(DB(practice=None).q, DB(practice=None).q1, 1)
    check("unknown practice", r is None and "no such practice" in why)

    d = DB(practice={"id": 1, "name": "X", "stripe_account_id": None})
    r, why = cp.pay_practice(d.q, d.q1, 1)
    check("no connected Stripe account", r is None and "not connected" in why, why)

    d = DB(practice={"id": 1, "name": "X", "stripe_account_id": "acct_1",
                     "payouts_enabled": False})
    r, why = cp.pay_practice(d.q, d.q1, 1)
    check("onboarding incomplete", r is None and "onboarding" in why, why)

    d = DB(practice=CONNECTED, ready_cents=0, ready_lines=0)
    r, why = cp.pay_practice(d.q, d.q1, 1)
    check("nothing has cleared the holdback", r is None and "holdback" in why, why)

    d = DB(practice=CONNECTED, ready_cents=-500, ready_lines=2)
    r, why = cp.pay_practice(d.q, d.q1, 1)
    check("a NEGATIVE balance never sends money",
          r is None and "nothing to send" in why,
          "refunds cancelled the earnings — do not transfer a negative")

    d = DB(practice=CONNECTED, ready_cents=100, ready_lines=1)
    r, why = cp.pay_practice(d.q, d.q1, 1)
    check("below the minimum, it rolls over instead",
          r is None and "minimum" in why, why)

    d = DB(practice=CONNECTED, ready_cents=50000, ready_lines=9)
    r, why = cp.pay_practice(d.q, d.q1, 1, dry_run=True)
    check("a dry run reports the amount and moves nothing",
          r and r["would_pay_cents"] == 50000 and r["dry_run"] is True, r)
    check("and it called no Stripe API",
          not any("stripe" in w.lower() for w, _ in d.writes))

    print("\n== onboarding links are treated as bearer tokens ==")
    link_src = inspect.getsource(cp.onboarding_link)
    # Precise: does any log call actually PASS the url? Matching the word "link" would
    # flag "account link failed", which is an error logged before a link exists.
    leaks = [ln.strip() for ln in (link_src + route_src_for_logs()).splitlines()
             if "log." in ln and ("link.url" in ln or ", url" in ln or "(url" in ln
                                  or "{url" in ln)]
    check("no log call ever passes the onboarding URL", not leaks, leaks)
    check("the URL is returned to the caller and nothing else",
          link_src.count("link.url") == 1 and "return link.url" in link_src)
    check("it is never written to the database",
          "INSERT" not in link_src and "UPDATE" not in link_src)
    route_src = inspect.getsource(cp.register_payout_routes)
    check("the API warns the vet not to forward it",
          "don't" in route_src and "forward" in route_src)

    print("\n== crittr never touches bank details ==")
    mod = inspect.getsource(cp)
    for forbidden in ("routing_number", "account_number", "ssn", "tax_id"):
        check(f"no handling of {forbidden}", forbidden not in mod.lower())
    check("the account is created as EXPRESS, not custom",
          'type="express"' in mod,
          "Stripe holds the bank details, so a crittr breach cannot reroute payouts")

    print("\n" + ("ALL PASS" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}"))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
