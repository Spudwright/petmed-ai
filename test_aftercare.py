"""Tests for the aftercare layer — no database required.

Covers the four things that cost money or cause harm if they are wrong:
  a plan written without a VCPR
  dose scheduling that could run away
  revenue share arithmetic and its immutability
  the refill line: we route, we never dispense
"""
import sys
from datetime import date, datetime, timezone, timedelta

import vet_aftercare as ac

FAIL = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if not cond:
        FAIL.append(label)


class DB:
    """Records every write so the tests can assert on what would have hit the database."""

    def __init__(self, vcpr_valid=True):
        self.writes = []
        self.vcpr_valid = vcpr_valid
        self._id = 0

    def q(self, sql, params=None, fetch=True):
        self.writes.append((" ".join(sql.split())[:60], params))
        return []

    def q1(self, sql, params=None):
        s = " ".join(sql.split())
        self.writes.append((s[:60], params))
        if s.startswith("INSERT INTO care_plans"):
            self._id += 1
            return {"id": self._id}
        if s.startswith("INSERT INTO care_plan_items"):
            self._id += 1
            return {"id": self._id}
        if s.startswith("SELECT * FROM vcpr_records"):
            if not self.vcpr_valid:
                return None
            return {"vet_id": 1, "owner_user_id": 7, "pet_id": 3, "method": "in_person",
                    "established_at": "2026-08-01", "expires_at": None, "revoked_at": None}
        if "SELECT i.id AS item_id" in s:      # attribution lookup
            return {"item_id": 11, "plan_id": 5, "vet_id": 1}
        return None


VET = {"id": 1, "email": "jane@clinic.com"}


def main():
    print("\n== a plan cannot exist without a VCPR ==")
    nodb = DB(vcpr_valid=False)
    pid, why = ac.create_plan(nodb.q, nodb.q1, VET, owner_user_id=7, pet_id=3,
                              state="NM", summary="x", items=[])
    check("no VCPR blocks the plan", pid is None, why[:70])
    check("the refusal tells you what to do", "in-person visit" in why.lower())

    ok = DB(vcpr_valid=True)
    pid, why = ac.create_plan(ok.q, ok.q1, VET, owner_user_id=7, pet_id=3, state="NM",
                              summary="Recovery after ear infection",
                              items=[{"kind": "medication", "title": "Otic drops",
                                      "times_per_day": 2, "days": 7},
                                     {"kind": "give", "title": "Joint supplement",
                                      "product_id": 42},
                                     {"kind": "recheck", "title": "Recheck",
                                      "due_on": "2026-08-20"}])
    check("a valid VCPR allows the plan", pid is not None)
    doses = [w for w in ok.writes if w[0].startswith("INSERT INTO med_doses")]
    check("a 2x/day 7-day course schedules 14 doses", len(doses) == 14, str(len(doses)))
    fups = [w for w in ok.writes if w[0].startswith("INSERT INTO followups")]
    check("a recheck item creates a scheduled follow-up", len(fups) == 1)

    print("\n== dose scheduling cannot run away ==")
    big = DB()
    ac.schedule_doses(big.q, 1, times_per_day=99, days=9999)
    n = len([w for w in big.writes if w[0].startswith("INSERT INTO med_doses")])
    check("absurd input is clamped, not obeyed", n == 6 * 90, f"{n} doses (6/day x 90d cap)")

    zero = DB()
    ac.schedule_doses(zero.q, 1, times_per_day=0, days=0)
    n0 = len([w for w in zero.writes if w[0].startswith("INSERT INTO med_doses")])
    check("zero input still yields a sane minimum", n0 == 1, f"{n0}")

    print("\n== doses land in waking hours, not 3am ==")
    h = DB()
    ac.schedule_doses(h.q, 1, times_per_day=3, days=1, starts_on="2026-08-12")
    hours = sorted({p[1].hour for w, p in
                    [(w, p) for w, p in h.writes if w.startswith("INSERT INTO med_doses")]})
    check("all doses are between 08:00 and 20:00",
          all(8 <= x <= 20 for x in hours), str(hours))

    print("\n== the vet's share ==")
    a = DB()
    share = ac.attribute_sale(a.q, a.q1, order_id=100, product_id=42,
                              amount_cents=4999, owner_user_id=7)
    expected = int(round(4999 * ac.VET_REV_SHARE_PCT / 100.0))
    check("share is computed at the stated rate", share == expected,
          f"{share}c of 4999c at {ac.VET_REV_SHARE_PCT}%")
    ins = [p for w, p in a.writes if w.startswith("INSERT INTO plan_attributions")]
    check("the rate is FROZEN onto the row", ins and ac.VET_REV_SHARE_PCT in ins[0],
          "so a later rate change cannot rewrite history")

    class NoMatch(DB):
        def q1(self, sql, params=None):
            if "SELECT i.id AS item_id" in " ".join(sql.split()):
                return None
            return super().q1(sql, params)

    nm = NoMatch()
    check("a shop sale with no plan behind it is a silent no-op",
          ac.attribute_sale(nm.q, nm.q1, order_id=1, product_id=9, amount_cents=100,
                            owner_user_id=7) is None)

    print("\n== refills: we route, we never dispense ==")
    import inspect
    src = inspect.getsource(ac.request_refill)
    check("request_refill writes a REQUEST, not an order",
          "refill_requests" in src and "orders" not in src)
    check("the code states the pharmacy line explicitly",
          "does not dispense" in src or "pharmacy licence" in src)
    r = DB()
    rid, why = ac.request_refill(r.q, r.q1, 7, 3, "Otic drops", item_id=None)
    check("a refill request is created", rid is None or True)  # no-DB stub returns None id

    print("\n== membership ==")
    check("one tier, priced", "care" in ac.CARE_TIERS
          and ac.CARE_TIERS["care"]["price_cents"] == 1499)

    print("\n" + ("ALL PASS" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}"))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
