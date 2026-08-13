"""Tests for the practice client book — no database required.

Every test here maps to one of the five invariants in vet_practice's docstring, because
each of those is a way this feature could take money it hasn't earned or contact someone
who never agreed to be contacted:

  1. an import without a signed attestation is refused
  2. an imported-but-unclaimed client earns the practice nothing
  3. an owner can leave
  4. claiming a second practice releases the first — never two clinics on one order
  5. the rate is frozen onto the row at sale time

Plus the two things that decide whether a clinic can actually use it: the CSV parser must
survive real PIMS exports, and plan attribution must outrank practice attribution so a
line item is never paid twice.
"""
import sys

import vet_practice as vpr

FAIL = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}"
          f"{(' — ' + str(detail)[:120]) if detail else ''}")
    if not cond:
        FAIL.append(label)


class DB:
    """Records every write so tests can assert on what would have hit the database."""

    def __init__(self, *, link=None, plan_match=False):
        self.writes = []
        self.link = link                # what practice_client_for_user returns
        self.plan_match = plan_match    # does a care plan name the product?
        self._id = 0

    def q(self, sql, params=None, fetch=True):
        self.writes.append((" ".join(sql.split()), params))
        return []

    def q1(self, sql, params=None):
        s = " ".join(sql.split())
        self.writes.append((s, params))
        if s.startswith("INSERT INTO practice_imports"):
            self._id += 1
            return {"id": self._id}
        if s.startswith("INSERT INTO practice_clients"):
            self._id += 1
            return {"id": self._id}
        if s.startswith("SELECT c.*, p.rev_share_pct"):
            return self.link
        if "SELECT i.id AS item_id" in s:          # aftercare's plan lookup
            return {"item_id": 11, "plan_id": 5, "vet_id": 1} if self.plan_match else None
        return None

    def inserts(self, table):
        """Params only — for values passed as placeholders."""
        return [p for w, p in self.writes if w.startswith(f"INSERT INTO {table}")]

    def insert_sql(self, table):
        """The statement text — for values written as SQL literals, like a status."""
        return [w for w, _ in self.writes if w.startswith(f"INSERT INTO {table}")]


VET = {"id": 1, "email": "jane@clinic.com"}
LINK = {"id": 9, "practice_id": 3, "vet_id": 1, "rev_share_pct": 15,
        "practice_name": "Sapillo Animal Hospital"}


def main():
    print("\n== invariant 1: an import is not a mailing list ==")
    db = DB()
    res, why = vpr.import_roster(db.q, db.q1, VET, 3,
                                 [{"email": "a@b.com"}], attested_by="")
    check("an import with no signed attestation is refused", res is None, why[:70])
    check("the refusal explains why a name is needed",
          "signed" in why or "attestation" in why)

    db = DB()
    res, why = vpr.import_roster(db.q, db.q1, VET, 3,
                                 [{"email": "a@b.com", "owner_name": "A B"}],
                                 attested_by="Jane Smith, DVM")
    check("a signed import is accepted", res is not None and res["added"] == 1)
    imp = db.inserts("practice_imports")
    check("the attestation TEXT is stored, not just a flag",
          imp and vpr.ATTESTATION in imp[0], "so what was agreed to is recoverable")
    check("the attesting human's name is stored", imp and "Jane Smith, DVM" in imp[0])
    sql = db.insert_sql("practice_clients")
    check("imported rows land as 'imported', never 'invited'",
          sql and "'imported'" in sql[0] and "'invited'" not in sql[0])
    sends = [w for w, _ in db.writes if "invited_at" in w]
    check("importing sends nothing to anybody", not sends)

    print("\n== invariant 2: attribution begins at CLAIM, not at import ==")
    unclaimed = DB(link=None)          # in the book, but has not accepted
    out = vpr.attribute_order(unclaimed.q, unclaimed.q1, order_id=1,
                              items=[{"product_id": 42, "price_cents": 4999,
                                      "quantity": 1}],
                              owner_user_id=7)
    check("an unclaimed client's order credits nobody", out["practice_cents"] == 0)
    check("and writes no attribution row", not unclaimed.inserts("plan_attributions"))

    claimed = DB(link=LINK)
    out = vpr.attribute_order(claimed.q, claimed.q1, order_id=2,
                              items=[{"product_id": 42, "price_cents": 4999,
                                      "quantity": 1}],
                              owner_user_id=7)
    claimed_cents = int(round(4999 * LINK["rev_share_pct"] / 100.0))
    check("a CLAIMED client's order credits the practice",
          out["practice_cents"] == claimed_cents,
          f"{out['practice_cents']}c of 4999c at {LINK['rev_share_pct']}%")

    print("\n== invariant 5: the rate is frozen onto the row ==")
    ins = claimed.inserts("plan_attributions")
    expected_cents = claimed_cents
    check("the row carries the pct it was calculated at",
          ins and LINK["rev_share_pct"] in ins[0])
    check("the row carries the cash amount too", ins and expected_cents in ins[0],
          f"{expected_cents}c")
    check("the row records WHY it was credited",
          claimed.insert_sql("plan_attributions")
          and "'practice'" in claimed.insert_sql("plan_attributions")[0])

    print("\n== ONE FLAT RATE: naming the product in a plan is worth nothing extra ==")
    both = DB(link=LINK, plan_match=True)
    out = vpr.attribute_order(both.q, both.q1, order_id=3,
                              items=[{"product_id": 42, "price_cents": 4999,
                                      "quantity": 1}],
                              owner_user_id=7)
    check("a planned product credits the vet", out["plan_cents"] > 0,
          f"{out['plan_cents']}c")
    check("and the practice is NOT also credited for it", out["practice_cents"] == 0,
          "one line, one payment")
    check("the planned rate EQUALS the relationship rate",
          out["plan_cents"] == claimed_cents,
          "no gradient = no incentive to write a product into a plan for the money")
    ins = both.inserts("plan_attributions")
    check("and the row is stamped with that same flat rate",
          ins and LINK["rev_share_pct"] in ins[0], f"{LINK['rev_share_pct']}%")

    print("\n== the vet earns on what was PAID, not on list price ==")
    class Discounted(DB):
        def q1(self, sql, params=None):
            if "credit_applied_cents" in " ".join(sql.split()):
                return {"d": 1000}          # a $10 referral credit was applied
            return super().q1(sql, params)

    disc = Discounted(link=LINK)
    out = vpr.attribute_order(disc.q, disc.q1, order_id=5,
                              items=[{"product_id": 42, "price_cents": 5000,
                                      "quantity": 1}],
                              owner_user_id=7)
    check("the discount is seen", out["discount_cents"] == 1000)
    check("credited on $40 paid, not $50 listed",
          out["practice_cents"] == int(round(4000 * 15 / 100.0)),
          f"{out['practice_cents']}c — list would have been {int(5000*15/100)}c")

    split = Discounted(link=LINK)
    out = vpr.attribute_order(split.q, split.q1, order_id=6,
                              items=[{"product_id": 1, "price_cents": 3000, "quantity": 1},
                                     {"product_id": 2, "price_cents": 1000, "quantity": 1}],
                              owner_user_id=7)
    rows = split.inserts("plan_attributions")
    amounts = sorted(r[5] for r in rows)
    check("an order-level discount is split across lines in proportion",
          amounts == [750, 2250], f"{amounts} from $30 + $10 less $10")
    check("and never pays out more than was collected",
          sum(amounts) == 3000, f"{sum(amounts)}c net of a 4000c order")

    print("\n== a refund claws the credit back ==")
    class Credited(DB):
        def __init__(self, rows, reversed_already=False):
            super().__init__()
            self.rows = rows
            self.rev = reversed_already
            self.notified = []

        def q(self, sql, params=None, fetch=True):
            s = " ".join(sql.split())
            self.writes.append((s, params))
            if "FROM plan_attributions a" in s:
                return self.rows
            return []

        def q1(self, sql, params=None):
            s = " ".join(sql.split())
            self.writes.append((s, params))
            if "source='reversal' LIMIT 1" in s:
                return {"x": 1} if self.rev else None
            if s.startswith("SELECT * FROM practices WHERE id"):
                self.notified.append(params)
                return {"id": 3, "name": "Sapillo", "contact_email": "front@clinic.example"}
            return None

    def row(payout_status=None):
        return [{"plan_id": None, "item_id": None, "vet_id": 1, "practice_id": 3,
                 "owner_user_id": 7, "product_id": 42, "amount_cents": 4000,
                 "share_pct": 15, "share_cents": 600, "payout_status": payout_status}]

    c = Credited(row())
    res = vpr.reverse_order(c.q, c.q1, order_id=5)
    check("a full refund reverses the whole credit", res["reversed_cents"] == 600, res)
    rev = c.inserts("plan_attributions")
    check("the reversal is a NEGATIVE row, not an edit",
          rev and -600 in rev[0] and -4000 in rev[0],
          "a statement shows the sale AND the reversal")
    check("no UPDATE or DELETE touched the original",
          not any(w.startswith(("UPDATE plan_attributions", "DELETE"))
                  for w, _ in c.writes))

    c = Credited(row())
    res = vpr.reverse_order(c.q, c.q1, order_id=5, refunded_cents=2000)
    check("a HALF refund reverses half the credit", res["reversed_cents"] == 300, res)
    check("and is marked partial", res.get("partial") is True)

    c = Credited(row(), reversed_already=True)
    res = vpr.reverse_order(c.q, c.q1, order_id=5)
    check("reversing twice claws back nothing the second time",
          res["reversed_cents"] == 0 and res["note"] == "already reversed")

    c = Credited([])
    res = vpr.reverse_order(c.q, c.q1, order_id=99)
    check("refunding an order nobody was credited on is a safe no-op",
          res["reversed_cents"] == 0, res["note"])

    print("\n== TIMING decides whether a refund is an event or just arithmetic ==")
    # Not yet paid out: the reversal nets off an open statement nobody has seen.
    unpaid = Credited(row(payout_status=None))
    res = vpr.reverse_order(unpaid.q, unpaid.q1, order_id=5)
    check("a refund BEFORE payout is netted off silently",
          res["reversed_cents"] == 600 and res["settled_cents"] == 0
          and res["carried"] is False, res)
    check("and the practice is NOT emailed about it", not unpaid.notified,
          "no notice about money they never had")

    pending = Credited(row(payout_status="pending"))
    res = vpr.reverse_order(pending.q, pending.q1, order_id=5)
    check("a statement that is CLOSED but not yet paid also just nets off",
          res["carried"] is False and res["settled_cents"] == 0, res)
    check("still no email", not pending.notified)

    # Already paid: crittr has sent money it is no longer owed.
    paid = Credited(row(payout_status="paid"))
    res = vpr.reverse_order(paid.q, paid.q1, order_id=5)
    check("a refund AFTER payout is a real clawback",
          res["settled_cents"] == 600 and res["carried"] is True, res)
    check("and THAT one notifies the practice", paid.notified == [(3,)],
          "an unexplained deduction next month is how you lose a clinic")
    carried = paid.inserts("plan_attributions")
    check("the debit carries with payout_id unset, so it lands on the NEXT statement",
          carried and "payout_id" not in paid.insert_sql("plan_attributions")[0],
          "it settles like any other line rather than needing a special case")

    print("\n== statements: closing one is what makes a later refund a clawback ==")
    import inspect
    src = inspect.getsource(vpr.close_statement)
    check("closing stamps every unpaid line with the payout id",
          "SET payout_id=%s" in src and "payout_id IS NULL" in src)
    check("closing does NOT move money", "stripe." not in src.lower(),
          "no payment API call — Stripe Connect is a separate step, this is only the ledger")
    paid_src = inspect.getsource(vpr.mark_payout_paid)
    check("marking paid is what flips later refunds into clawbacks",
          "status='paid'" in paid_src)

    print("\n== quantity is honoured, and free lines are skipped ==")
    qty = DB(link=LINK)
    out = vpr.attribute_order(qty.q, qty.q1, order_id=4,
                              items=[{"product_id": 42, "price_cents": 1000,
                                      "quantity": 3},
                                     {"product_id": 43, "price_cents": 0, "quantity": 1},
                                     {"name": "Shipping", "price_cents": 599}],
                              owner_user_id=7)
    check("3 x $10 credits on $30, not $10", out["practice_cents"] == 450,
          f"{out['practice_cents']}c")
    check("a zero-priced line credits nothing", out["lines"] == 1)
    check("shipping and tax have no product_id and are ignored",
          len(qty.inserts("plan_attributions")) == 1)

    print("\n== Stripe retries the webhook: crediting must be idempotent ==")

    class AlreadyCredited(DB):
        def q1(self, sql, params=None):
            if "FROM plan_attributions WHERE order_id" in " ".join(sql.split()):
                return {"x": 1}
            return super().q1(sql, params)

    retry = AlreadyCredited(link=LINK)
    out = vpr.attribute_order(retry.q, retry.q1, order_id=2,
                              items=[{"product_id": 42, "price_cents": 4999,
                                      "quantity": 1}],
                              owner_user_id=7)
    check("a repeated webhook credits nothing a second time",
          out["practice_cents"] == 0 and out.get("skipped") == "already credited")
    check("and writes no second row", not retry.inserts("plan_attributions"),
          "one purchase, one payment to the clinic")

    print("\n== the CSV parser survives real PIMS exports ==")
    rows, probs = vpr.parse_roster(
        "Client Name,Primary Email,Patient,Last Visit\n"
        "Maria Ortiz,maria@example.com,Rufus,2026-07-14\n"
        "Dan Webb,dan@example.com,Nala,07/22/2026\n")
    check("Cornerstone-style headers are mapped", len(rows) == 2 and not probs)
    check("owner, pet and email all land",
          rows[0]["email"] == "maria@example.com"
          and rows[0]["owner_name"] == "Maria Ortiz"
          and rows[0]["pet_name"] == "Rufus")

    rows, probs = vpr.parse_roster("E-Mail;Owner;Pet\nx@y.com;A;B\n")
    check("a semicolon-delimited export still parses", len(rows) == 1, str(probs))

    rows, probs = vpr.parse_roster("Owner,Pet\nMaria,Rufus\n")
    check("a file with no email column is refused, with the headers echoed",
          not rows and probs and "Owner" in probs[0], probs[0][:60] if probs else "")

    rows, probs = vpr.parse_roster(
        "email,owner\ngood@example.com,A\nnot-an-email,B\n,C\n")
    check("bad rows are REPORTED, not silently dropped",
          len(rows) == 1 and len(probs) == 2, f"{len(rows)} kept, {len(probs)} reported")

    rows, probs = vpr.parse_roster("")
    check("an empty file fails safely rather than raising", not rows)

    rows, _ = vpr.parse_roster("email\nMARIA@Example.COM\n")
    check("emails are normalised to lower case", rows[0]["email"] == "maria@example.com",
          "so the same client isn't imported twice under two spellings")

    print("\n== invariant 4: one owner, one practice ==")
    import inspect
    src = inspect.getsource(vpr.claim)
    check("claiming releases any other live link",
          "status='released'" in src and "status='claimed' AND id<>" in src)

    print("\n== invariant 3: leaving is one click ==")
    rel = inspect.getsource(vpr.release)
    check("release only marks the link, never touches the user's own data",
          "practice_clients" in rel and "DELETE" not in rel.upper())

    print("\n== a declined invitation is never re-sent ==")
    dec = DB()

    class Declined(DB):
        def q1(self, sql, params=None):
            if "WHERE c.claim_token" in " ".join(sql.split()):
                return {"id": 1, "practice_id": 3, "status": "declined"}
            return super().q1(sql, params)

    d = Declined()
    row, why = vpr.claim(d.q, d.q1, "tok", 7)
    check("a declined invite cannot be claimed later", row is None, why[:50])
    inv_src = inspect.getsource(vpr.invite_clients)
    check("invites only ever select status='imported'",
          inv_src.count("status='imported'") == 2,
          "so invited/claimed/declined are never mailed again")

    print("\n== the claim token never leaks in a list response ==")
    ser = vpr._ser({"id": 1, "email": "a@b.com", "claim_token": "SECRET"})
    check("_ser strips claim_token", "claim_token" not in ser)

    print("\n" + ("ALL PASS" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}"))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
