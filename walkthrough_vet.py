"""A veterinarian walks the whole crittr partner flow, against a REAL Postgres.

The unit tests use in-memory fakes, which means the schema in vet_practice.init_practice_tables
had never actually been executed by a database until this script existed. This runs the real
DDL, the real SQL and the real HTTP routes end to end, in the order a clinic would meet them:

  apply -> refused while pending -> verified -> refused while the state is closed ->
  state activated -> practice created -> client book imported -> invited -> owner claims ->
  owner buys -> the practice is credited -> a care plan outranks that -> the chart assistant
  -> the owner leaves and the credit stops

Run:  DATABASE_URL=... python walkthrough_vet.py
Sends no email (RESEND_API_KEY is deliberately left unset) and touches no production data.
"""
import os
import sys
import json

os.environ.setdefault("ADMIN_USER", "drclaude")
os.environ.setdefault("ADMIN_PASS", "test-only-password")
os.environ.pop("RESEND_API_KEY", None)          # no real email leaves this machine
os.environ.setdefault("SECRET_KEY", "walkthrough")

import app as A                                  # noqa: E402
import vet_compliance as vc                      # noqa: E402
import vet_practice as vpr                       # noqa: E402
import vet_portal as vp                          # noqa: E402
import vet_aftercare as ac                       # noqa: E402
import vet_ai as vai                             # noqa: E402

FAIL = []
STEP = [0]


def step(title):
    STEP[0] += 1
    print(f"\n\033[1m{STEP[0]}. {title}\033[0m")


def check(label, cond, detail=""):
    print(f"   {'PASS' if cond else 'FAIL'}  {label}{('  — ' + str(detail)[:110]) if detail else ''}")
    if not cond:
        FAIL.append(label)


ADMIN = ("drclaude", "test-only-password")


def main():
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL is required"); return 2

    step("Create the schema on a real Postgres (the DDL has never run before)")
    # Re-runnable: this is a throwaway database, and a walkthrough you can only run once is
    # a walkthrough nobody runs twice.
    A.q("""DROP TABLE IF EXISTS plan_attributions, practice_clients, practice_imports,
           practices, med_doses, care_plan_items, followups, refill_requests, care_plans,
           care_members, vcpr_records, vet_cases, vet_licenses, vet_audit, vets,
           vet_state_rules, vet_compliance_audit, orders, pets, users CASCADE""",
        fetch=False)
    A.init_db()
    # `products` is deliberately NOT dropped above — init_db only seeds an empty catalogue,
    # and re-seeding on every run would be slow. But that means product state SURVIVES,
    # including anything another test left behind. This bit me: the economics test marks
    # affiliate products rev_share_eligible=FALSE, after which this walkthrough's purchase
    # step correctly credited nothing and looked like an attribution regression. State the
    # preconditions rather than inheriting them.
    A.q("UPDATE products SET rev_share_eligible = TRUE, cost_cents = NULL", fetch=False)
    vp.init_vet_tables(A.q)
    ac.init_aftercare_tables(A.q)
    vpr.init_practice_tables(A.q)
    tables = {r["tablename"] for r in A.q(
        "SELECT tablename FROM pg_tables WHERE schemaname='public'") or []}
    for t in ("vets", "vet_licenses", "vet_cases", "vcpr_records", "vet_audit",
              "care_plans", "care_plan_items", "med_doses", "plan_attributions",
              "practices", "practice_clients", "practice_imports"):
        check(f"table {t} exists", t in tables)
    cols = {r["column_name"] for r in A.q(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='plan_attributions'") or []}
    check("plan_attributions gained source/practice_id/owner_user_id",
          {"source", "practice_id", "owner_user_id"} <= cols, sorted(cols))
    idx = {r["indexname"] for r in A.q(
        "SELECT indexname FROM pg_indexes WHERE tablename='plan_attributions'") or []}
    check("the one-credit-per-line unique index was created",
          "idx_attr_order_line" in idx, sorted(idx))

    vet_c = A.app.test_client()
    own_c = A.app.test_client()

    step("Dr. Claude creates an account and applies to partner")
    r = vet_c.post("/api/register", json={"email": "dr.claude@sapillo.example",
                                          "password": "hunter2!", "name": "Dr. Claude"})
    check("account created", r.status_code == 200, r.get_json())
    r = vet_c.post("/api/vet/apply", json={
        "full_name": "Dr. Claude, DVM", "clinic_name": "Sapillo Animal Hospital",
        "state": "NM", "license_number": "NM-4242", "expires_on": "2028-06-30",
        "email": "dr.claude@sapillo.example", "phone": "575-555-0142"})
    j = r.get_json()
    check("application accepted and lands PENDING", j.get("status") == "pending", j.get("next"))

    step("Pending means pending — no cases, no practice, no charts")
    check("case queue refused", vet_c.get("/api/vet/cases").status_code == 403)
    check("practice refused", vet_c.get("/api/vet/practice").status_code == 403)
    check("chart refused", vet_c.get("/api/vet/chart?owner_user_id=1").status_code == 403)

    step("A crittr admin verifies the licence (a human act, recorded by name)")
    vet_id = A.q1("SELECT id FROM vets WHERE email=%s",
                  ("dr.claude@sapillo.example",))["id"]
    adm = A.app.test_client()
    r = adm.post(f"/api/admin/vets/{vet_id}/verify",
                 json={"actor": "MBD", "notes": "checked NM board 2026-08-12"},
                 auth=ADMIN)
    check("verification succeeded", r.status_code == 200, r.get_json())
    row = A.q1("SELECT status, verified_by FROM vets WHERE id=%s", (vet_id,))
    check("the verifier's NAME is stored, not just a flag", row["verified_by"] == "MBD")

    step("Verified, but New Mexico is still closed — default deny holds")
    r = vet_c.get("/api/vet/me")
    j = r.get_json()
    check("account is verified", r.status_code == 200 and j["vet"]["status"] == "verified")
    check("but NO active states yet", j["active_states"] == [], j["active_states"])
    note = vet_c.get("/api/vet/cases").get_json().get("note") or ""
    check("and the queue explains why rather than just showing nothing",
          "licence" in note and "permitted" in note, note)

    step("MBD activates New Mexico for routing (prescribing left OFF)")
    r = adm.post("/api/admin/compliance/NM/activate", json={
        "actor": "Dr. Claude, DVM (NM licence NM-4242)",
        "answers": {"routing_allowed": True, "telemedicine_vcpr_allowed": False,
                    "rx_allowed": False},
        "note": "walkthrough"}, auth=ADMIN)
    check("NM activated", r.status_code == 200, r.get_json().get("state", {}).get("status"))
    j = vet_c.get("/api/vet/me").get_json()
    check("Dr. Claude is now active in NM", j["active_states"] == ["NM"], j["active_states"])

    step("Dr. Claude sets up the practice")
    r = vet_c.post("/api/vet/practice", json={
        "name": "Sapillo Animal Hospital", "state": "NM",
        "contact_email": "front.desk@sapillo.example", "phone": "575-555-0142"})
    check("practice created", r.status_code == 200, r.get_json())
    practice_id = int(r.get_json()["practice"]["id"])
    check("it carries the default revenue share",
          int(r.get_json()["practice"]["rev_share_pct"]) == vpr.REV_SHARE_PCT,
          f"{vpr.REV_SHARE_PCT}%")

    step("The client book: an unsigned upload is REFUSED")
    csv = ("Client Name,Primary Email,Patient,Species,Last Visit\n"
           "Maria Ortiz,maria.ortiz@example.com,Rufus,dog,2026-07-14\n"
           "Dan Webb,dan.webb@example.com,Nala,dog,2026-06-30\n"
           "Priya Raman,priya.raman@example.com,Biscuit,cat,2026-08-01\n"
           "Broken Row,not-an-email,Ghost,dog,\n")
    r = vet_c.post("/api/vet/practice/import", data={"csv": csv, "attested_by": ""})
    check("no signed attestation = refused", r.status_code == 400, r.get_json().get("error"))
    check("nothing was written", (A.q1("SELECT COUNT(*) n FROM practice_clients")["n"]) == 0)

    step("Signed, it imports — and reports the row it could not use")
    r = vet_c.post("/api/vet/practice/import",
                   data={"csv": csv, "attested_by": "Dr. Claude, DVM"})
    j = r.get_json()
    check("3 clients imported", j.get("added") == 3, j)
    check("the bad row is REPORTED, not silently dropped",
          j.get("problem_count") == 1, j.get("problems"))
    check("the attestation text is stored with the name",
          vpr.ATTESTATION in (A.q1("SELECT attestation FROM practice_imports LIMIT 1")
                              or {}).get("attestation", ""))
    statuses = {r["status"] for r in A.q("SELECT DISTINCT status FROM practice_clients")}
    check("every row is 'imported' — nothing was contacted", statuses == {"imported"},
          statuses)

    step("Re-uploading the same book does not duplicate anyone")
    r = vet_c.post("/api/vet/practice/import",
                   data={"csv": csv, "attested_by": "Dr. Claude, DVM"})
    j = r.get_json()
    check("0 added, 3 recognised as already present",
          j.get("added") == 0 and j.get("skipped") == 3, j)

    step("Invitations go out (no RESEND key here, so nothing is actually emailed)")
    r = vet_c.post("/api/vet/practice/invite", json={})
    j = r.get_json()
    check("3 selected", j.get("selected") == 3, j)
    check("0 delivered — the key is unset, and that is reported honestly",
          j.get("sent") == 0 and j.get("failed") == 3,
          "THIS IS THE PRODUCTION TRAP: set RESEND_API_KEY or invites silently no-op")
    invited = A.q("SELECT email, status, claim_token FROM practice_clients "
                  "WHERE status='invited'")
    check("all three now hold a claim token", len(invited) == 3 and
          all(i["claim_token"] for i in invited))

    step("The claim token never leaks through the API")
    body = vet_c.get("/api/vet/practice/clients").get_data(as_text=True)
    check("no token in the client list response",
          all(i["claim_token"] not in body for i in invited))

    step("Maria (an existing client) clicks her invitation")
    maria = next(i for i in invited if i["email"] == "maria.ortiz@example.com")
    tok = maria["claim_token"]
    r = own_c.get(f"/vet/claim/{tok}")
    check("the claim page renders", r.status_code == 200)
    check("it names her practice and her pet",
          b"Sapillo Animal Hospital" in r.data and b"Rufus" in r.data)
    r = own_c.post("/api/practice/claim", json={"token": tok})
    check("claiming while signed out asks her to sign in first",
          r.status_code == 401 and r.get_json().get("needs_auth"))

    own_c.post("/api/register", json={"email": "maria.ortiz@example.com",
                                      "password": "rufus123", "name": "Maria Ortiz"})
    r = own_c.post("/api/practice/claim", json={"token": tok})
    check("after signing up, the claim succeeds", r.status_code == 200, r.get_json())
    check("she sees her practice",
          own_c.get("/api/practice/me").get_json().get("practice")
          == "Sapillo Animal Hospital")
    maria_id = A.q1("SELECT id FROM users WHERE email=%s",
                    ("maria.ortiz@example.com",))["id"]
    own_c.post("/api/pets", json={"name": "Rufus", "species": "dog", "breed": "heeler",
                                  "weight_lbs": 38, "age_years": 4,
                                  "conditions": "recurrent otitis"})
    pet = A.q1("SELECT id FROM pets WHERE user_id=%s", (maria_id,))
    pet_id = pet["id"] if pet else None

    step("A used token cannot be replayed")
    r = own_c.post("/api/practice/claim", json={"token": tok})
    check("the token is dead after use", r.status_code == 400, r.get_json().get("error"))

    step("Maria buys flea & tick — the practice is credited")
    prod = A.q1("SELECT id, name, price_cents FROM products WHERE in_stock=TRUE LIMIT 1")
    check("a real product exists to buy", bool(prod), prod)
    order = A.q1("""INSERT INTO orders (user_id, status, items, subtotal_cents, total_cents)
                    VALUES (%s,'paid',%s::jsonb,%s,%s) RETURNING id""",
                 (maria_id, json.dumps([{"product_id": prod["id"], "name": prod["name"],
                                         "price_cents": prod["price_cents"],
                                         "quantity": 2}]),
                  prod["price_cents"] * 2, prod["price_cents"] * 2))
    out = vpr.attribute_order(A.q, A.q1, order_id=order["id"],
                              items=[{"product_id": prod["id"],
                                      "price_cents": prod["price_cents"], "quantity": 2}],
                              owner_user_id=maria_id)
    gross = prod["price_cents"] * 2
    expect = int(round(gross * vpr.REV_SHARE_PCT / 100.0))
    check(f"credited {vpr.REV_SHARE_PCT}% of the LINE (qty 2), not the unit price",
          out["practice_cents"] == expect, f"{out['practice_cents']}c of {gross}c")
    row = A.q1("SELECT * FROM plan_attributions WHERE order_id=%s", (order["id"],))
    check("the row records source='practice'", row["source"] == "practice")
    check("the rate is frozen onto the row", row["share_pct"] == vpr.REV_SHARE_PCT)

    step("Stripe retries the webhook — it must not pay twice")
    out2 = vpr.attribute_order(A.q, A.q1, order_id=order["id"],
                              items=[{"product_id": prod["id"],
                                      "price_cents": prod["price_cents"], "quantity": 2}],
                              owner_user_id=maria_id)
    check("the retry credits nothing", out2.get("skipped") == "already credited", out2)
    n = A.q1("SELECT COUNT(*) n FROM plan_attributions WHERE order_id=%s",
             (order["id"],))["n"]
    check("still exactly one attribution row", n == 1, n)

    step("Dr. Claude records the in-person visit, then writes a care plan")
    vet_row = dict(A.q1("SELECT * FROM vets WHERE id=%s", (vet_id,)))
    rec, why = vp.establish_vcpr(A.q, A.q1, vet_row, maria_id, pet_id, "in_person", "NM")
    check("an in-person VCPR is recorded", rec is not None, why)
    plan_id, why = ac.create_plan(A.q, A.q1, vet_row, owner_user_id=maria_id, pet_id=pet_id,
                                  state="NM", summary="Otitis recheck plan",
                                  items=[{"kind": "medication", "title": "Otic drops",
                                          "times_per_day": 2, "days": 10},
                                         {"kind": "give", "title": prod["name"],
                                          "product_id": prod["id"]}])
    check("the plan is created", plan_id is not None, why)
    doses = A.q1("SELECT COUNT(*) n FROM med_doses")["n"]
    check("2x/day for 10 days scheduled 20 doses", doses == 20, doses)

    step("She re-orders the SAME product, now named in a care plan — SAME flat rate")
    o2 = A.q1("""INSERT INTO orders (user_id, status, items, subtotal_cents, total_cents)
                 VALUES (%s,'paid','[]'::jsonb,%s,%s) RETURNING id""",
              (maria_id, prod["price_cents"], prod["price_cents"]))
    out3 = vpr.attribute_order(A.q, A.q1, order_id=o2["id"],
                               items=[{"product_id": prod["id"],
                                       "price_cents": prod["price_cents"], "quantity": 1}],
                               owner_user_id=maria_id)
    flat = int(round(prod["price_cents"] * vpr.REV_SHARE_PCT / 100.0))
    check(f"credited at the flat {vpr.REV_SHARE_PCT}%",
          out3["plan_cents"] == flat, f"{out3['plan_cents']}c")
    check("and NOT also credited to the practice", out3["practice_cents"] == 0,
          "one line, one payment")
    per_unit = int(round(prod["price_cents"] * vpr.REV_SHARE_PCT / 100.0))
    check("writing it into a plan earned exactly what buying it would have",
          out3["plan_cents"] == per_unit,
          "no gradient — the vet is never paid more for naming a product")
    n2 = A.q1("SELECT COUNT(*) n FROM plan_attributions WHERE order_id=%s",
              (o2["id"],))["n"]
    check("exactly one row for that line", n2 == 1, n2)

    step("A discounted order pays on what was CHARGED, not on list price")
    o_d = A.q1("""INSERT INTO orders (user_id, status, items, subtotal_cents,
                                      total_cents, credit_applied_cents)
                  VALUES (%s,'paid','[]'::jsonb,5000,4000,1000) RETURNING id""",
               (maria_id,))
    outd = vpr.attribute_order(A.q, A.q1, order_id=o_d["id"],
                               items=[{"product_id": prod["id"], "price_cents": 5000,
                                       "quantity": 1}],
                               owner_user_id=maria_id)
    net_expect = int(round(4000 * vpr.REV_SHARE_PCT / 100.0))
    credited = outd["plan_cents"] + outd["practice_cents"]
    check("a $10 credit came off before the share was calculated",
          credited == net_expect,
          f"{credited}c on $40 paid — list would have been "
          f"{int(5000 * vpr.REV_SHARE_PCT / 100)}c")
    amt = A.q1("SELECT amount_cents FROM plan_attributions WHERE order_id=%s",
               (o_d["id"],))["amount_cents"]
    check("and the row records the NET value it was calculated on", amt == 4000, amt)

    step("Maria returns it — the credit is clawed back")
    before = A.q1("SELECT COALESCE(SUM(share_cents),0) c FROM plan_attributions")["c"]
    res = vpr.reverse_order(A.q, A.q1, order_id=o_d["id"], reason="charge.refunded")
    after = A.q1("SELECT COALESCE(SUM(share_cents),0) c FROM plan_attributions")["c"]
    check("the reversal removed exactly what the sale added",
          before - after == net_expect, f"{before} -> {after}")
    rev = A.q1("""SELECT * FROM plan_attributions
                  WHERE order_id=%s AND source='reversal'""", (o_d["id"],))
    check("it is a NEGATIVE row, not an edit", rev and rev["share_cents"] == -net_expect)
    orig = A.q1("""SELECT * FROM plan_attributions
                   WHERE order_id=%s AND source <> 'reversal'""", (o_d["id"],))
    check("the original sale row is untouched — the statement shows both",
          orig and orig["share_cents"] == net_expect)
    res2 = vpr.reverse_order(A.q, A.q1, order_id=o_d["id"])
    check("a duplicate refund webhook claws back nothing more",
          res2["reversed_cents"] == 0, res2.get("note"))
    check("and because it had NOT been paid out, it was silent",
          res["carried"] is False and res["settled_cents"] == 0,
          "netted off an open statement — no email about money she never had")

    step("Month end: the statement is closed and paid")
    st = vpr.open_statement(A.q, A.q1, practice_id)
    payout, why = vpr.close_statement(A.q, A.q1, practice_id, reference="AUG-2026")
    check("the payout froze exactly what was owed",
          payout and payout["amount_cents"] == st["owed_cents"],
          f"{st['owed_cents']}c across {st['lines']} lines")
    check("the open statement is now empty",
          vpr.open_statement(A.q, A.q1, practice_id)["owed_cents"] == 0)
    unstamped = A.q1("""SELECT COUNT(*) n FROM plan_attributions
                        WHERE practice_id=%s AND payout_id IS NULL""", (practice_id,))["n"]
    check("every line is stamped with the payout", unstamped == 0, unstamped)
    vpr.mark_payout_paid(A.q, A.q1, payout["id"], reference="ACH-12345")
    check("and the money is marked as gone",
          A.q1("SELECT status FROM practice_payouts WHERE id=%s",
               (payout["id"],))["status"] == "paid")

    step("NOW a refund lands on an already-paid sale — that one is a real clawback")
    res3 = vpr.reverse_order(A.q, A.q1, order_id=order["id"], reason="charge.refunded")
    check("it is flagged as carried, not netted",
          res3["carried"] is True and res3["settled_cents"] == expect, res3)
    st2 = vpr.open_statement(A.q, A.q1, practice_id)
    check("the debit lands on the NEXT statement as a negative",
          st2["owed_cents"] == -expect, f"{st2['owed_cents']}c owed")
    check("and it is visible as an adjustment, not a mystery",
          st2["adjustments_cents"] == -expect, st2)
    check("the already-paid payout is NOT retroactively altered",
          A.q1("SELECT amount_cents FROM practice_payouts WHERE id=%s",
               (payout["id"],))["amount_cents"] == payout["amount_cents"],
          "history stays what it was; the correction is a new line")

    step("The earnings dashboard adds up, net of both reversals")
    e = vet_c.get("/api/vet/practice/earnings").get_json()
    # Three sales happened; two were refunded. Only the un-refunded plan sale should stand.
    check("two refunded sales net to zero, leaving only the sale that stuck",
          e["total_cents"] == flat,
          f"{e['total_cents']}c — the one order nobody sent back")
    ledger = A.q1("SELECT COALESCE(SUM(share_cents),0) c FROM plan_attributions")["c"]
    check("the dashboard equals the ledger, with nothing hidden",
          e["total_cents"] == ledger, f"dashboard {e['total_cents']}c vs ledger {ledger}c")
    check("it is split by WHY it was earned",
          {"practice", "plan"} <= set(e["by_source"]), e["by_source"])
    check("reversals appear as their own negative line, not a silent deduction",
          e["by_source"].get("reversal", {}).get("cents", 0) < 0,
          e["by_source"].get("reversal"))
    check("and the open statement shows the carried debit a clinic will ask about",
          e["open_statement"]["adjustments_cents"] < 0, e["open_statement"])

    step("The chart assistant: Dr. Claude reads HIS OWN patient")
    r = vet_c.get(f"/api/vet/chart?owner_user_id={maria_id}&pet_id={pet_id}")
    check("chart allowed for his connected client", r.status_code == 200)
    rendered = r.get_json()["rendered"]
    check("it names the patient", "Rufus" in rendered)
    check("it carries the known condition", "recurrent otitis" in rendered)
    check("it states VCPR validity", "VCPR: valid" in rendered)
    check("it shows the course as never started",
          "NONE of 20 doses marked given" in rendered, "the most useful line in the chart")
    check("it shows what she actually bought", prod["name"] in rendered)

    step("A DIFFERENT vet tries to read the same patient")
    other = A.app.test_client()
    other.post("/api/register", json={"email": "dr.other@example.com",
                                      "password": "hunter2!", "name": "Dr. Other"})
    other.post("/api/vet/apply", json={"full_name": "Dr. Other, DVM", "state": "NM",
                                       "license_number": "NM-9999",
                                       "email": "dr.other@example.com"})
    oid = A.q1("SELECT id FROM vets WHERE email=%s", ("dr.other@example.com",))["id"]
    adm.post(f"/api/admin/vets/{oid}/verify", json={"actor": "MBD"}, auth=ADMIN)
    r = other.get(f"/api/vet/chart?owner_user_id={maria_id}&pet_id={pet_id}")
    check("a verified, NM-licensed stranger is REFUSED", r.status_code == 403,
          r.get_json().get("error"))
    check("the refusal explains the rule",
          "not yours to read" in r.get_json().get("error", ""))

    step("Every chart read is in the audit log")
    vet_c.post("/api/vet/chart/brief",
               json={"owner_user_id": maria_id, "pet_id": pet_id})
    a = A.q("SELECT action, actor FROM vet_audit ORDER BY id")
    actions = [x["action"] for x in a]
    for want in ("vet_applied", "vet_verified", "practice_created",
                 "practice_roster_imported", "practice_clients_invited",
                 "practice_client_claimed", "vcpr_established", "care_plan_created"):
        check(f"audited: {want}", want in actions)

    step("Maria disconnects — and the credit stops")
    # Counted before, compared after: the point is that leaving DELETES nothing of hers,
    # and a hard-coded number only tests that I can still count.
    before_pets = A.q1("SELECT COUNT(*) n FROM pets WHERE user_id=%s", (maria_id,))["n"]
    before_orders = A.q1("SELECT COUNT(*) n FROM orders WHERE user_id=%s",
                         (maria_id,))["n"]
    r = own_c.post("/api/practice/release")
    check("she can leave in one call", r.status_code == 200, r.get_json())
    check("she is no longer connected",
          own_c.get("/api/practice/me").get_json()["connected"] is False)
    after_pets = A.q1("SELECT COUNT(*) n FROM pets WHERE user_id=%s", (maria_id,))["n"]
    after_orders = A.q1("SELECT COUNT(*) n FROM orders WHERE user_id=%s", (maria_id,))["n"]
    check("her pets and orders are untouched",
          after_pets == before_pets >= 1 and after_orders == before_orders >= 1,
          f"{before_pets} pets / {before_orders} orders, unchanged")
    check("and her account still exists",
          bool(A.q1("SELECT id FROM users WHERE id=%s", (maria_id,))))
    o3 = A.q1("""INSERT INTO orders (user_id, status, items, subtotal_cents, total_cents)
                 VALUES (%s,'paid','[]'::jsonb,100,100) RETURNING id""", (maria_id,))
    out4 = vpr.attribute_order(A.q, A.q1, order_id=o3["id"],
                               items=[{"product_id": 999999, "price_cents": 100,
                                       "quantity": 1}],
                               owner_user_id=maria_id)
    check("a purchase after leaving credits the practice nothing",
          out4["practice_cents"] == 0 and out4["plan_cents"] == 0, out4)
    r = vet_c.get(f"/api/vet/chart?owner_user_id={maria_id}&pet_id={pet_id}")
    check("but Dr. Claude keeps chart access via the VCPR he legitimately holds",
          r.status_code == 200, "leaving the commercial link != revoking the clinical one")

    print("\n" + ("\033[1mALL PASS\033[0m" if not FAIL
                  else f"\033[1m{len(FAIL)} FAILED:\033[0m " + "; ".join(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
