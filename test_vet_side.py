"""Tests for the vet side — run without a database.

These are the invariants that matter because breaking one means practising veterinary
medicine somewhere we are not permitted to, or showing a minor's data to the wrong person.
They use a tiny in-memory fake of the app's q()/q1() helpers so they run anywhere.
"""
import sys
from datetime import date, timedelta

import vet_compliance as vc

FAIL = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if not cond:
        FAIL.append(label)


class FakeDB:
    """Enough of a database to exercise the compliance gate honestly."""

    def __init__(self):
        self.states = {}

    def q(self, sql, params=None, fetch=True):
        s = " ".join(sql.split())
        if s.startswith("CREATE") or s.startswith("INSERT INTO vet_compliance_audit"):
            return []
        if s.startswith("INSERT INTO vet_state_rules"):
            code = params[0]
            self.states.setdefault(code, {"state": code, "status": "unverified"})
            return []
        if s.startswith("UPDATE vet_state_rules SET"):
            code = params[-1]
            row = self.states.setdefault(code, {"state": code})
            assigns = s[len("UPDATE vet_state_rules SET "):s.index(" WHERE")].split(", ")
            vals = list(params[:-1])
            for a in assigns:
                field = a.split("=")[0].strip()
                if "NOW()" in a:
                    row[field] = "now"
                    continue
                row[field] = vals.pop(0)
            return []
        return []

    def q1(self, sql, params=None):
        s = " ".join(sql.split())
        if s.startswith("SELECT * FROM vet_state_rules"):
            return self.states.get(params[0])
        return None


def main():
    db = FakeDB()

    print("\n== default deny: the whole rollout rests on this ==")
    ok, why = vc.is_allowed(db.q1, "NM", vc.ACTION_ROUTE_CASE)
    check("an unknown state denies routing", not ok, why[:70])
    ok, why = vc.is_allowed(db.q1, "NM", vc.ACTION_PRESCRIBE)
    check("an unknown state denies prescribing", not ok)

    vc.seed_state(db.q, "NM")
    ok, why = vc.is_allowed(db.q1, "NM", vc.ACTION_ROUTE_CASE)
    check("a SEEDED but unreviewed state still denies", not ok, why[:70])
    check("the refusal names what to do next", "review" in why.lower() or "activate" in why.lower())

    print("\n== a state is only opened by a NAMED human ==")
    try:
        vc.activate_state(db.q, db.q1, "NM", {"routing_allowed": True}, actor="")
        check("activation without an actor is refused", False)
    except ValueError as e:
        check("activation without an actor is refused", True, str(e)[:50])

    print("\n== partial opening: triage on, prescribing off ==")
    # The realistic first launch: route cases, do NOT prescribe.
    vc.activate_state(db.q, db.q1, "NM",
                      {"routing_allowed": True,
                       "telemedicine_vcpr_allowed": False,
                       "rx_allowed": False},
                      actor="Dr J. Example, DVM (NM licence 1234)",
                      note="confirmed on call 2026-08-10")
    ok, _ = vc.is_allowed(db.q1, "NM", vc.ACTION_ROUTE_CASE)
    check("routing is now allowed in NM", ok)
    ok, why = vc.is_allowed(db.q1, "NM", vc.ACTION_PRESCRIBE)
    check("prescribing is STILL denied in NM", not ok, why[:60])
    ok, why = vc.is_allowed(db.q1, "NM", vc.ACTION_ESTABLISH_VCPR_REMOTE)
    check("remote VCPR is STILL denied in NM", not ok)

    print("\n== one state does not open another ==")
    ok, why = vc.is_allowed(db.q1, "TX", vc.ACTION_ROUTE_CASE)
    check("activating NM leaves TX closed", not ok, why[:60])

    print("\n== an unanswered question is never a yes ==")
    vc.seed_state(db.q, "AZ")
    vc.activate_state(db.q, db.q1, "AZ", {"routing_allowed": True}, actor="tester")
    ok, why = vc.is_allowed(db.q1, "AZ", vc.ACTION_PRESCRIBE)
    check("rx_allowed left unanswered blocks prescribing",
          not ok and "not answered" in why, why[:60])

    print("\n== the kill switch ==")
    vc.suspend_state(db.q, "NM", "ops", "board enquiry")
    ok, why = vc.is_allowed(db.q1, "NM", vc.ACTION_ROUTE_CASE)
    check("suspending NM stops routing immediately", not ok, why[:40])

    print("\n== the checklist is actionable ==")
    cl = vc.state_checklist(db.q1, "AZ")
    blocking = [x["field"] for x in cl["questions"] if x["blocking"]]
    check("checklist lists the unanswered questions", len(blocking) >= 3,
          ", ".join(blocking[:3]))
    check("checklist reports per-action permission", "prescribe" in cl["actions"])

    print("\n== licence expiry is enforced, not decorative ==")
    # active_states_for_vet intersects licence validity with state status.
    class LicDB(FakeDB):
        def __init__(self, expires):
            super().__init__()
            self._expires = expires

        def q(self, sql, params=None, fetch=True):
            if "FROM vet_licenses" in sql:
                return [{"state": "NM", "license_number": "1234",
                         "expires_on": self._expires, "verified_at": "yes"}]
            return super().q(sql, params, fetch)

    import vet_portal as vp
    past = LicDB(date.today() - timedelta(days=1))
    vc.seed_state(past.q, "NM")
    vc.activate_state(past.q, past.q1, "NM", {"routing_allowed": True}, actor="t")
    check("an EXPIRED licence yields no states",
          vp.active_states_for_vet(past.q, past.q1, 1) == [])

    future = LicDB(date.today() + timedelta(days=365))
    vc.seed_state(future.q, "NM")
    vc.activate_state(future.q, future.q1, "NM", {"routing_allowed": True}, actor="t")
    check("a VALID licence in an ACTIVE state yields NM",
          vp.active_states_for_vet(future.q, future.q1, 1) == ["NM"])

    unver = LicDB(date.today() + timedelta(days=365))
    vc.seed_state(unver.q, "NM")   # seeded but never activated
    check("a valid licence in an UNVERIFIED state yields nothing",
          vp.active_states_for_vet(unver.q, unver.q1, 1) == [])

    print("\n" + ("ALL PASS" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}"))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
