"""Tests for the chart assistant — no database, no LLM call.

Almost all of this file is about ONE thing: a vet must never read the record of an animal
they have no relationship with. Everything else here is presentation; that is the part that
would be a genuine privacy breach, so it is tested from both directions — that the two
legitimate doors open, and that nothing else does.

The rest covers the promise the system prompt makes: this summarises a record, it does not
practise medicine.
"""
import sys
from datetime import datetime, timezone

import vet_ai as vai

FAIL = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if not cond:
        FAIL.append(label)


class DB:
    def __init__(self, *, link=None, vcpr=None):
        self.link = link        # practice_client_for_user result
        self.vcpr = vcpr        # vcpr_records row
        self.reads = []

    def q(self, sql, params=None, fetch=True):
        self.reads.append(" ".join(sql.split()))
        return []

    def q1(self, sql, params=None):
        s = " ".join(sql.split())
        self.reads.append(s)
        if s.startswith("SELECT c.*, p.rev_share_pct"):
            return self.link
        if s.startswith("SELECT * FROM vcpr_records"):
            return self.vcpr
        return None


VET = {"id": 1, "email": "jane@clinic.com"}
OTHER_VET_LINK = {"id": 9, "practice_id": 3, "vet_id": 99, "rev_share_pct": 8,
                  "practice_name": "Some Other Clinic"}
OWN_LINK = {"id": 9, "practice_id": 3, "vet_id": 1, "rev_share_pct": 8,
            "practice_name": "Sapillo Animal Hospital"}
LIVE_VCPR = {"vet_id": 1, "owner_user_id": 7, "pet_id": 3, "method": "in_person",
             "established_at": "2026-08-01", "expires_at": None, "revoked_at": None}
REVOKED_VCPR = dict(LIVE_VCPR, revoked_at="2026-08-05")


def main():
    print("\n== the access gate: only two doors open ==")
    ok, why = vai.may_view(DB().q, DB().q1, VET, 7, 3)
    check("no practice link and no VCPR = refused", not ok, why[:60])
    check("the refusal says whose record it is not", "not yours to read" in why)

    d = DB(link=OWN_LINK)
    ok, _ = vai.may_view(d.q, d.q1, VET, 7, 3)
    check("door 1: a claimed client of MY practice opens it", ok)

    d = DB(vcpr=LIVE_VCPR)
    ok, _ = vai.may_view(d.q, d.q1, VET, 7, 3)
    check("door 2: a live VCPR with me opens it", ok)

    print("\n== and nothing else does ==")
    d = DB(link=OTHER_VET_LINK)
    ok, why = vai.may_view(d.q, d.q1, VET, 7, 3)
    check("another practice's claimed client is REFUSED", not ok,
          "being a crittr vet is not access to every crittr client")

    d = DB(vcpr=REVOKED_VCPR)
    ok, why = vai.may_view(d.q, d.q1, VET, 7, 3)
    check("a REVOKED VCPR is refused", not ok, why[:40])

    # A real datetime, as psycopg2 hands back — vcpr_status compares it to now().
    d = DB(vcpr=dict(LIVE_VCPR,
                     expires_at=datetime(2020, 1, 1, tzinfo=timezone.utc)))
    ok, why = vai.may_view(d.q, d.q1, VET, 7, 3)
    check("an EXPIRED VCPR is refused", not ok, why[:40])

    ok, why = vai.may_view(DB().q, DB().q1, VET, None, None)
    check("no owner at all is refused", not ok)

    print("\n== the gate runs BEFORE any record is read ==")
    d = DB()                       # no access
    chart, why = vai.build_chart(d.q, d.q1, VET, 7, 3)
    check("a refused chart is None", chart is None)
    leaked = [r for r in d.reads if "FROM pets" in r or "FROM vet_cases" in r
              or "FROM orders" in r]
    check("NOTHING about the patient was queried", not leaked,
          "the refusal costs zero rows, so a denied vet learns nothing")

    d = DB(link=OWN_LINK)
    chart, why = vai.build_chart(d.q, d.q1, VET, 7, 3)
    check("an allowed chart is assembled", chart is not None and why == "")
    for t in ("FROM pets", "FROM vet_cases", "FROM care_plans", "FROM followups",
              "FROM refill_requests", "FROM orders"):
        check(f"it reads {t.split()[1]}", any(t in r for r in d.reads))

    print("\n== ask() refuses before it ever reaches the model ==")
    d = DB()
    ans, why = vai.ask(d.q, d.q1, VET, owner_user_id=7, pet_id=3, question="how is he?")
    check("no access = no answer", ans is None and "not yours to read" in why)
    check("and no LLM call was attempted", not any("audit" in r.lower() for r in d.reads))

    d = DB(link=OWN_LINK)
    ans, why = vai.ask(d.q, d.q1, VET, owner_user_id=7, pet_id=3, question="")
    check("an empty question is refused", ans is None and "ask a question" in why)

    print("\n== the record is rendered, not invented ==")
    empty = vai.render_chart({"pet": None, "pets": [], "vcpr": {"valid": False,
                                                               "reason": "none"}})
    check("an empty record says so rather than padding",
          "VCPR" in empty and len(empty) < 200, f"{len(empty)} chars")

    rendered = vai.render_chart({
        "pet": {"name": "Rufus", "species": "dog", "breed": "heeler", "age_years": 4,
                "weight_lbs": 38, "conditions": "recurrent otitis"},
        "vcpr": {"valid": True, "method": "in_person", "established_at": "2026-08-01"},
        "plan_items": [{"kind": "medication", "title": "Otic drops", "times_per_day": 2,
                        "days": 10, "due": 20, "given": 6,
                        "last_given": datetime(2026, 8, 4, tzinfo=timezone.utc)}],
        "orders": [{"created_at": "2026-08-02", "items":
                    '[{"name":"Otic drops","quantity":1}]'}],
    })
    check("adherence is stated as counts, not adjectives",
          "6 of 20 doses marked given" in rendered, "a vet can act on a number")
    check("the last dose date is present", "04 Aug 2026" in rendered)
    check("known conditions carry through", "recurrent otitis" in rendered)
    check("what they bought is included",
          "Otic drops x1" in rendered and "adherence signal" in rendered)

    none_given = vai.render_chart({"plan_items": [
        {"kind": "medication", "title": "X", "due": 14, "given": 0}]})
    check("a course never started is called out explicitly",
          "NONE of 14 doses marked given" in none_given, "the single most useful line in the chart")

    print("\n== it summarises; it does not prescribe ==")
    check("the system prompt forbids diagnosing",
          "do NOT diagnose" in vai.SYSTEM.replace("Do NOT", "do NOT"))
    check("the system prompt forbids dose changes",
          "adjust a medication or dose" in vai.SYSTEM)
    check("the system prompt forbids inventing facts",
          "never fill a gap" in vai.SYSTEM)
    check("it is told who it is talking to",
          "licensed veterinarian" in vai.SYSTEM and "not the owner" in vai.SYSTEM)

    print("\n== every chart read is auditable ==")
    import inspect
    check("ask() writes to the vet audit log",
          "vp.audit" in inspect.getsource(vai.ask),
          "who looked at whose record, and when")

    print("\n" + ("ALL PASS" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}"))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
