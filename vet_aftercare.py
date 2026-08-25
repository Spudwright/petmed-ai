"""crittr.ai — everything that happens AFTER the visit.

WHY THIS IS THE WHOLE BUSINESS. Both New Mexico and Texas require a VCPR to be established
by an in-person examination. That looks like a restriction and is actually the shape of the
product: crittr triages at 2am, sends the owner to a real clinic, and the moment that
appointment happens a VCPR exists — after which telemedicine with THAT vet is legal. So the
regulated part is the visit, and everything downstream of it is open.

Five things live here, in the order they earn their keep:

  1. THE POST-VISIT PLAN. The vet taps three things at the end of the appointment — what to
     feed, what to give, when to recheck — and the owner gets it as a plan with buy buttons
     and reminders. It is the VET'S recommendation, not our upsell, which is the entire
     reason it converts.
  2. MEDICATION ADHERENCE. Owners forget doses. Reminders and a "did you give it?" check,
     with the vet notified if adherence slips. No regulatory exposure at all: reminding is
     not prescribing.
  3. THE FOLLOW-UP CHECK-IN. Days after the visit, back to the SAME vet. Legal because the
     VCPR now exists — gated on exactly that, per state.
  4. REFILL ROUTING. We route the REQUEST to the prescribing vet. We never dispense. That is
     a deliberate line: dispensing needs a pharmacy licence, and it is why the fake Rx
     generics were removed in Phase H.10.
  5. crittr CARE. The subscription that bundles 1-4. Last on purpose — bundling before the
     parts exist is selling a promise.

THE COMMERCIAL POINT, which is also the pitch to the vet: clinics currently LOSE product
revenue. The vet says "start him on a joint supplement", the owner buys it from Chewy, the
clinic sees nothing. Attributing that sale back to the recommending vet gives them income
they had written off — and that, far more than referrals, is what makes a partner stay.
"""
import os
import json
import logging
from datetime import datetime, timedelta, timezone

from flask import request, jsonify, session

import vet_compliance as vc
import vet_portal as vp

log = logging.getLogger("crittr.aftercare")

# What share of a plan-attributed sale is credited to the recommending vet. Stored on every
# line at the time of sale rather than read live, so changing the rate never rewrites
# history — a clinic must be able to reconcile last month against what they were told then.
VET_REV_SHARE_PCT = int(os.environ.get("CRITTR_REV_SHARE_PCT",
                                       os.environ.get("CRITTR_VET_REV_SHARE_PCT", "10")))

ITEM_FEED = "feed"          # a food / diet instruction
ITEM_GIVE = "give"          # a supplement or OTC to administer
ITEM_MED = "medication"     # something the vet dispensed at the visit — adherence tracked
ITEM_RECHECK = "recheck"    # a follow-up appointment or check-in

PLAN_ACTIVE = "active"
PLAN_COMPLETE = "complete"
PLAN_CANCELLED = "cancelled"


def init_aftercare_tables(q):
    """Create the aftercare schema. Safe to call repeatedly."""
    q("""
    CREATE TABLE IF NOT EXISTS care_plans (
        id              SERIAL PRIMARY KEY,
        case_id         INTEGER REFERENCES vet_cases(id) ON DELETE SET NULL,
        vet_id          INTEGER REFERENCES vets(id) ON DELETE SET NULL,
        owner_user_id   INTEGER NOT NULL,
        pet_id          INTEGER,
        state           CHAR(2),
        summary         TEXT,
        status          TEXT NOT NULL DEFAULT 'active',
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        completed_at    TIMESTAMPTZ
    )""", fetch=False)
    q("""
    CREATE TABLE IF NOT EXISTS care_plan_items (
        id              SERIAL PRIMARY KEY,
        plan_id         INTEGER NOT NULL REFERENCES care_plans(id) ON DELETE CASCADE,
        kind            TEXT NOT NULL,          -- feed | give | medication | recheck
        title           TEXT NOT NULL,
        instructions    TEXT,
        product_id      INTEGER REFERENCES products(id) ON DELETE SET NULL,
        -- Adherence: how often, for how long. NULL frequency means "no reminders".
        times_per_day   INTEGER,
        days            INTEGER,
        starts_on       DATE,
        -- Recheck items carry a date instead of a schedule.
        due_on          DATE,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""", fetch=False)
    q("""
    CREATE TABLE IF NOT EXISTS med_doses (
        id              SERIAL PRIMARY KEY,
        item_id         INTEGER NOT NULL REFERENCES care_plan_items(id) ON DELETE CASCADE,
        due_at          TIMESTAMPTZ NOT NULL,
        given_at        TIMESTAMPTZ,
        skipped         BOOLEAN DEFAULT FALSE,
        UNIQUE (item_id, due_at)
    )""", fetch=False)
    q("""
    CREATE TABLE IF NOT EXISTS followups (
        id              SERIAL PRIMARY KEY,
        plan_id         INTEGER REFERENCES care_plans(id) ON DELETE CASCADE,
        vet_id          INTEGER REFERENCES vets(id) ON DELETE SET NULL,
        owner_user_id   INTEGER NOT NULL,
        pet_id          INTEGER,
        state           CHAR(2),
        due_on          DATE,
        owner_message   TEXT,
        photo_url       TEXT,
        vet_reply       TEXT,
        status          TEXT NOT NULL DEFAULT 'scheduled',  -- scheduled|open|answered
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        answered_at     TIMESTAMPTZ
    )""", fetch=False)
    q("""
    CREATE TABLE IF NOT EXISTS refill_requests (
        id              SERIAL PRIMARY KEY,
        item_id         INTEGER REFERENCES care_plan_items(id) ON DELETE SET NULL,
        vet_id          INTEGER REFERENCES vets(id) ON DELETE SET NULL,
        owner_user_id   INTEGER NOT NULL,
        pet_id          INTEGER,
        medication      TEXT NOT NULL,
        note            TEXT,
        status          TEXT NOT NULL DEFAULT 'requested',  -- requested|approved|declined
        vet_note        TEXT,
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        decided_at      TIMESTAMPTZ
    )""", fetch=False)
    q("""
    CREATE TABLE IF NOT EXISTS plan_attributions (
        id              SERIAL PRIMARY KEY,
        plan_id         INTEGER REFERENCES care_plans(id) ON DELETE SET NULL,
        item_id         INTEGER REFERENCES care_plan_items(id) ON DELETE SET NULL,
        vet_id          INTEGER REFERENCES vets(id) ON DELETE SET NULL,
        order_id        INTEGER,
        product_id      INTEGER,
        amount_cents    INTEGER NOT NULL,
        -- Frozen at sale time: a clinic must be able to reconcile last month against the
        -- rate they were quoted then, not whatever it is today.
        share_pct       INTEGER NOT NULL,
        share_cents     INTEGER NOT NULL,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""", fetch=False)
    q("""
    CREATE TABLE IF NOT EXISTS care_members (
        id              SERIAL PRIMARY KEY,
        user_id         INTEGER NOT NULL UNIQUE,
        tier            TEXT NOT NULL DEFAULT 'care',
        status          TEXT NOT NULL DEFAULT 'active',
        started_at      TIMESTAMPTZ DEFAULT NOW(),
        ends_at         TIMESTAMPTZ,
        stripe_sub_id   TEXT
    )""", fetch=False)
    q("CREATE INDEX IF NOT EXISTS idx_plans_owner ON care_plans(owner_user_id, status)",
      fetch=False)
    q("CREATE INDEX IF NOT EXISTS idx_doses_due ON med_doses(due_at) WHERE given_at IS NULL",
      fetch=False)


# ── 1. the post-visit plan ───────────────────────────────────────────────────

def create_plan(q, q1, vet, *, owner_user_id, pet_id, state, summary, items, case_id=None):
    """The vet writes the plan. Three taps at the end of an appointment.

    Requires a live VCPR: this is post-visit care from the vet who saw the animal, and
    without that relationship it is neither legal nor meaningful. Returns (plan_id, reason).
    """
    v = vp.vcpr_status(q1, vet["id"], owner_user_id, pet_id)
    if not v["valid"]:
        return None, (f"no valid vet-client-patient relationship on record — {v['reason']}. "
                      f"Record the in-person visit first.")
    row = q1("""INSERT INTO care_plans
                (case_id, vet_id, owner_user_id, pet_id, state, summary, status)
                VALUES (%s,%s,%s,%s,%s,%s,'active') RETURNING id""",
             (case_id, vet["id"], owner_user_id, pet_id,
              (state or "").upper()[:2], (summary or "").strip()))
    if not row:
        return None, "could not create the plan"
    plan_id = row["id"]
    for it in (items or []):
        kind = (it.get("kind") or ITEM_GIVE).strip().lower()
        item = q1("""INSERT INTO care_plan_items
                     (plan_id, kind, title, instructions, product_id, times_per_day, days,
                      starts_on, due_on)
                     VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                  (plan_id, kind, (it.get("title") or "").strip(),
                   (it.get("instructions") or "").strip(), it.get("product_id"),
                   it.get("times_per_day"), it.get("days"),
                   it.get("starts_on"), it.get("due_on")))
        # A medication with a schedule gets its doses generated up front, so a reminder is
        # a row that exists rather than a calculation someone has to remember to run.
        if item and kind == ITEM_MED and it.get("times_per_day") and it.get("days"):
            schedule_doses(q, item["id"], int(it["times_per_day"]), int(it["days"]),
                           it.get("starts_on"))
        # A recheck becomes a scheduled follow-up automatically.
        if item and kind == ITEM_RECHECK and it.get("due_on"):
            q("""INSERT INTO followups (plan_id, vet_id, owner_user_id, pet_id, state,
                                        due_on, status)
                 VALUES (%s,%s,%s,%s,%s,%s,'scheduled')""",
              (plan_id, vet["id"], owner_user_id, pet_id, (state or "").upper()[:2],
               it["due_on"]), fetch=False)
    vp.audit(q, "care_plan_created", actor=vet["email"], vet_id=vet["id"], case_id=case_id,
             detail={"plan_id": plan_id, "items": len(items or [])})
    return plan_id, ""


def get_plan(q, q1, plan_id):
    plan = q1("SELECT * FROM care_plans WHERE id=%s", (plan_id,))
    if not plan:
        return None
    items = q("""SELECT i.*, p.name AS product_name, p.price_cents, p.image_url, p.slug
                 FROM care_plan_items i
                 LEFT JOIN products p ON p.id = i.product_id
                 WHERE i.plan_id=%s ORDER BY
                   CASE i.kind WHEN 'medication' THEN 0 WHEN 'give' THEN 1
                        WHEN 'feed' THEN 2 ELSE 3 END, i.id""", (plan_id,)) or []
    return {"plan": plan, "items": items}


def plans_for_owner(q, owner_user_id, status=PLAN_ACTIVE):
    return q("""SELECT p.*, v.full_name AS vet_name, v.clinic_name
                FROM care_plans p LEFT JOIN vets v ON v.id = p.vet_id
                WHERE p.owner_user_id=%s AND p.status=%s
                ORDER BY p.created_at DESC""", (owner_user_id, status)) or []


# ── 2. medication adherence ──────────────────────────────────────────────────

def schedule_doses(q, item_id, times_per_day, days, starts_on=None):
    """Materialise every dose. Bounded deliberately so a typo cannot create a million rows."""
    times_per_day = max(1, min(6, int(times_per_day)))
    days = max(1, min(90, int(days)))
    start = starts_on or datetime.now(timezone.utc).date()
    if isinstance(start, str):
        start = datetime.strptime(start[:10], "%Y-%m-%d").date()
    # Spread doses across waking hours rather than round the clock — 8am to 8pm.
    first_hour, span = 8, 12
    step = span / times_per_day if times_per_day > 1 else 0
    for d in range(days):
        for n in range(times_per_day):
            hour = int(first_hour + step * n)
            due = datetime(start.year, start.month, start.day, hour, 0,
                           tzinfo=timezone.utc) + timedelta(days=d)
            q("""INSERT INTO med_doses (item_id, due_at) VALUES (%s,%s)
                 ON CONFLICT (item_id, due_at) DO NOTHING""", (item_id, due), fetch=False)


def mark_dose(q, q1, dose_id, given=True):
    q("""UPDATE med_doses SET given_at = CASE WHEN %s THEN NOW() ELSE NULL END,
                              skipped  = CASE WHEN %s THEN FALSE ELSE TRUE END
         WHERE id=%s""", (given, given, dose_id), fetch=False)
    return q1("SELECT * FROM med_doses WHERE id=%s", (dose_id,))


def due_doses(q, owner_user_id, within_hours=12):
    """What the owner owes right now — the screen that makes crittr a daily habit."""
    return q("""SELECT d.*, i.title, i.instructions, i.plan_id
                FROM med_doses d
                JOIN care_plan_items i ON i.id = d.item_id
                JOIN care_plans p ON p.id = i.plan_id
                WHERE p.owner_user_id=%s AND p.status='active'
                  AND d.given_at IS NULL AND d.skipped = FALSE
                  AND d.due_at <= NOW() + (%s || ' hours')::interval
                ORDER BY d.due_at ASC""", (owner_user_id, str(int(within_hours)))) or []


def adherence(q1, item_id):
    """Percentage taken so far. The number that tells a vet whether the course worked."""
    row = q1("""SELECT COUNT(*) AS total,
                       COUNT(given_at) AS given,
                       COUNT(*) FILTER (WHERE skipped) AS skipped
                FROM med_doses WHERE item_id=%s AND due_at <= NOW()""", (item_id,))
    total = int((row or {}).get("total") or 0)
    given = int((row or {}).get("given") or 0)
    return {"due_so_far": total, "given": given,
            "skipped": int((row or {}).get("skipped") or 0),
            "pct": round(100 * given / total) if total else None}


# ── 3. the follow-up check-in ────────────────────────────────────────────────

def open_followup(q, q1, followup_id, owner_message, photo_url=None):
    """Owner answers 'how's he doing?'. Gated on the VCPR still being live."""
    f = q1("SELECT * FROM followups WHERE id=%s", (followup_id,))
    if not f:
        return None, "no such follow-up"
    v = vp.vcpr_status(q1, f["vet_id"], f["owner_user_id"], f.get("pet_id"))
    if not v["valid"]:
        return None, (f"this follow-up needs a live relationship with the vet who saw your "
                      f"pet — {v['reason']}")
    q("""UPDATE followups SET owner_message=%s, photo_url=%s, status='open' WHERE id=%s""",
      ((owner_message or "").strip(), photo_url, followup_id), fetch=False)
    return q1("SELECT * FROM followups WHERE id=%s", (followup_id,)), ""


def answer_followup(q, q1, vet, followup_id, reply):
    f = q1("SELECT * FROM followups WHERE id=%s", (followup_id,))
    if not f:
        return None, "no such follow-up"
    if f.get("vet_id") != vet["id"]:
        return None, "that follow-up belongs to another veterinarian"
    q("""UPDATE followups SET vet_reply=%s, status='answered', answered_at=NOW()
         WHERE id=%s""", ((reply or "").strip(), followup_id), fetch=False)
    vp.audit(q, "followup_answered", actor=vet["email"], vet_id=vet["id"],
             detail={"followup_id": followup_id})
    return q1("SELECT * FROM followups WHERE id=%s", (followup_id,)), ""


# ── 4. refill routing (we route, we never dispense) ──────────────────────────

def request_refill(q, q1, owner_user_id, pet_id, medication, item_id=None, note=""):
    """Send a refill REQUEST to the prescribing vet.

    crittr does not dispense and does not hold a pharmacy licence. This creates a request
    the vet actions in their own system — the line is deliberate and load-bearing.
    """
    vet_id = None
    if item_id:
        row = q1("""SELECT p.vet_id FROM care_plan_items i
                    JOIN care_plans p ON p.id = i.plan_id WHERE i.id=%s""", (item_id,))
        vet_id = (row or {}).get("vet_id")
    r = q1("""INSERT INTO refill_requests
              (item_id, vet_id, owner_user_id, pet_id, medication, note, status)
              VALUES (%s,%s,%s,%s,%s,%s,'requested') RETURNING id""",
           (item_id, vet_id, owner_user_id, pet_id, (medication or "").strip(),
            (note or "").strip()))
    return (r or {}).get("id"), ""


def decide_refill(q, q1, vet, refill_id, approve, vet_note=""):
    r = q1("SELECT * FROM refill_requests WHERE id=%s", (refill_id,))
    if not r:
        return None, "no such request"
    if r.get("vet_id") not in (None, vet["id"]):
        return None, "that request belongs to another veterinarian"
    q("""UPDATE refill_requests SET status=%s, vet_note=%s, decided_at=NOW(), vet_id=%s
         WHERE id=%s""",
      ("approved" if approve else "declined", (vet_note or "").strip(), vet["id"],
       refill_id), fetch=False)
    vp.audit(q, "refill_decided", actor=vet["email"], vet_id=vet["id"],
             detail={"refill_id": refill_id, "approved": bool(approve)})
    return q1("SELECT * FROM refill_requests WHERE id=%s", (refill_id,)), ""


# ── attribution: the vet's share of what their advice sells ──────────────────

def attribute_sale(q, q1, *, order_id, product_id, amount_cents, owner_user_id,
                   share_pct=None):
    """Credit a sale to the vet who recommended that product, if one did.

    Matched on an ACTIVE plan for that owner containing that product. Silent no-op when
    nothing matches, because most shop sales have no plan behind them and that is fine.

    `share_pct` is the rate to pay. This used to be fixed here at a HIGHER number than a
    plain relationship sale earned, which meant a veterinarian was paid more for having
    made a clinical decision about a particular product. That gradient is gone: the caller
    passes one flat rate for every line of the order, and this function's only remaining
    job is deciding WHETHER a plan named this product — not what that is worth.

    `amount_cents` must be what the customer actually paid for the line, net of discounts.
    """
    row = q1("""SELECT i.id AS item_id, i.plan_id, p.vet_id
                FROM care_plan_items i
                JOIN care_plans p ON p.id = i.plan_id
                WHERE p.owner_user_id=%s AND p.status='active' AND i.product_id=%s
                ORDER BY p.created_at DESC LIMIT 1""", (owner_user_id, product_id))
    if not row or not row.get("vet_id"):
        return None
    pct = int(VET_REV_SHARE_PCT if share_pct is None else share_pct)
    share = int(round(int(amount_cents) * pct / 100.0))
    q("""INSERT INTO plan_attributions
         (plan_id, item_id, vet_id, order_id, product_id, amount_cents, share_pct,
          share_cents)
         VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
         ON CONFLICT DO NOTHING""",
      (row["plan_id"], row["item_id"], row["vet_id"], order_id, product_id,
       int(amount_cents), pct, share), fetch=False)
    return share


def vet_earnings(q, q1, vet_id, days=30):
    row = q1("""SELECT COALESCE(SUM(share_cents),0) AS cents, COUNT(*) AS n
                FROM plan_attributions
                WHERE vet_id=%s AND created_at > NOW() - (%s || ' days')::interval""",
             (vet_id, str(int(days))))
    return {"days": days, "orders": int((row or {}).get("n") or 0),
            "earned_cents": int((row or {}).get("cents") or 0)}


# ── 5. crittr Care ───────────────────────────────────────────────────────────

# The membership is the business, not a side plan: the practice earns a share of it every
# month the client stays (see member_plan), which is what makes bringing a client book
# across worth doing. Price is read from env so it cannot drift away from the Stripe Price
# that actually does the charging.
#
# "10% off everything in the shop" used to be listed here and has been removed. Every
# product in the catalogue is an affiliate link that checks out on Amazon, where no crittr
# discount can be applied — it was a promise the checkout could not keep.
CARE_TIERS = {
    "care": {"label": "crittr Care",
             "price_cents": int(os.environ.get("CRITTR_MEMBER_PRICE_CENTS", "1999")),
             "includes": ["Unlimited AI triage, day or night",
                          "Unlimited messaging with your own veterinary practice",
                          f"{int(os.environ.get('CRITTR_MEMBER_CONSULTS_INCLUDED', '2'))}"
                          " video consults a month included",
                          "Further consults at your vet's own rate",
                          "Medication reminders and refill routing",
                          "Your pet's at-home record, kept in one place"]},
}


def is_member(q1, user_id):
    row = q1("""SELECT * FROM care_members WHERE user_id=%s AND status='active'
                AND (ends_at IS NULL OR ends_at > NOW())""", (user_id,))
    return bool(row), (row or {})


def start_membership(q, q1, user_id, tier="care", stripe_sub_id=None, months=1):
    ends = datetime.now(timezone.utc) + timedelta(days=31 * max(1, int(months)))
    q("""INSERT INTO care_members (user_id, tier, status, ends_at, stripe_sub_id)
         VALUES (%s,%s,'active',%s,%s)
         ON CONFLICT (user_id) DO UPDATE SET tier=EXCLUDED.tier, status='active',
             ends_at=EXCLUDED.ends_at, stripe_sub_id=EXCLUDED.stripe_sub_id""",
      (user_id, tier, ends, stripe_sub_id), fetch=False)
    return is_member(q1, user_id)[1]


# ── nightly: reminders and lapsed-adherence alerts ───────────────────────────

def nightly_aftercare(q, q1, dry_run=False):
    """Called from nightly_jobs. Returns a summary dict.

    Two jobs: open today's scheduled follow-ups, and flag medication courses where the
    owner has fallen behind so the vet hears about it while it still matters.
    """
    opened = q("""UPDATE followups SET status='open'
                  WHERE status='scheduled' AND due_on <= CURRENT_DATE
                  RETURNING id""") or []
    lapsed = q("""SELECT i.id AS item_id, i.title, p.vet_id, p.owner_user_id,
                         COUNT(*) FILTER (WHERE d.given_at IS NULL) AS missed
                  FROM med_doses d
                  JOIN care_plan_items i ON i.id = d.item_id
                  JOIN care_plans p ON p.id = i.plan_id
                  WHERE p.status='active' AND d.due_at < NOW() - interval '12 hours'
                        AND d.given_at IS NULL AND d.skipped = FALSE
                  GROUP BY i.id, i.title, p.vet_id, p.owner_user_id
                  HAVING COUNT(*) >= 3""") or []
    sent = {"owner_dose_digests": 0, "followup_emails": 0, "vet_alerts": 0}
    if not dry_run:
        import care_notify as cn
        for r in lapsed:
            vp.audit(q, "adherence_lapsed", actor="system", vet_id=r.get("vet_id"),
                     detail={"item_id": r["item_id"], "missed": int(r["missed"]),
                             "title": r.get("title")})
        # ONE DIGEST PER OWNER PER DAY, never one email per dose. A twice-daily fortnight
        # course would otherwise send 28 emails, the owner unsubscribes on day two, and
        # then misses the message that mattered. The cap is the feature.
        by_owner = {}
        for r in lapsed:
            by_owner.setdefault(r.get("owner_user_id"), []).append(r)
        for owner_id, rows in by_owner.items():
            if owner_id and cn.notify_owner_doses(q, q1, owner_id, rows):
                sent["owner_dose_digests"] += 1
        by_vet = {}
        for r in lapsed:
            if r.get("vet_id"):
                by_vet.setdefault(r["vet_id"], []).append(r)
        for vet_id, rows in by_vet.items():
            if cn.notify_vet_lapsed(q, q1, vet_id, rows):
                sent["vet_alerts"] += 1
        # Follow-ups only notify on the day they open, so nobody is nagged twice.
        for f in q("""SELECT * FROM followups WHERE status='open'
                      AND due_on = CURRENT_DATE""") or []:
            if cn.notify_owner_followup(q, q1, f):
                sent["followup_emails"] += 1
    return {"followups_opened": len(opened), "adherence_alerts": len(lapsed), "sent": sent}


# ── HTTP surface ─────────────────────────────────────────────────────────────

def register_aftercare_routes(app, q, q1):
    vet_only = vp.require_vet(q1)

    # -- vet writes the plan --------------------------------------------------
    @app.route("/api/vet/plans", methods=["POST"])
    @vet_only
    def vet_create_plan(vet):
        d = request.get_json(silent=True) or {}
        plan_id, why = create_plan(
            q, q1, vet,
            owner_user_id=d.get("owner_user_id"), pet_id=d.get("pet_id"),
            state=d.get("state"), summary=d.get("summary"),
            items=d.get("items") or [], case_id=d.get("case_id"))
        if not plan_id:
            return jsonify({"error": why}), 403
        return jsonify({"ok": True, "plan_id": plan_id,
                        "plan": _ser(get_plan(q, q1, plan_id))})

    @app.route("/api/vet/cases/<int:case_id>/plan", methods=["POST"])
    @vet_only
    def vet_plan_from_case(vet, case_id):
        """Write a plan straight off a case the vet just reviewed.

        The composer used to demand an owner_user_id and pet_id, which a real vet has no
        way of knowing and would have to look up — a form that only works for whoever
        built it. The case already carries both, so the vet supplies clinical content and
        nothing else.
        """
        case = q1("SELECT * FROM vet_cases WHERE id=%s", (case_id,))
        if not case:
            return jsonify({"error": "no such case"}), 404
        if case.get("assigned_vet_id") != vet["id"]:
            return jsonify({"error": "that case is not assigned to you"}), 403
        d = request.get_json(silent=True) or {}
        plan_id, why = create_plan(
            q, q1, vet,
            owner_user_id=case.get("owner_user_id"), pet_id=case.get("pet_id"),
            state=case.get("state"), summary=d.get("summary"),
            items=d.get("items") or [], case_id=case_id)
        if not plan_id:
            return jsonify({"error": why}), 403
        return jsonify({"ok": True, "plan_id": plan_id,
                        "plan": _ser(get_plan(q, q1, plan_id))})

    @app.route("/api/vet/cases/mine", methods=["GET"])
    @vet_only
    def vet_my_cases(vet):
        """Cases this vet has claimed or reviewed — what the composer picks from."""
        rows = q("""SELECT * FROM vet_cases WHERE assigned_vet_id=%s
                    AND status IN ('claimed','reviewed')
                    ORDER BY claimed_at DESC NULLS LAST LIMIT 30""", (vet["id"],)) or []
        return jsonify({"cases": [_ser(r) for r in rows]})

    @app.route("/api/vet/earnings", methods=["GET"])
    @vet_only
    def vet_earnings_route(vet):
        e = vet_earnings(q, q1, vet["id"], int(request.args.get("days", 30)))
        e["share_pct"] = VET_REV_SHARE_PCT
        return jsonify(e)

    @app.route("/api/vet/followups", methods=["GET"])
    @vet_only
    def vet_followups(vet):
        rows = q("""SELECT * FROM followups WHERE vet_id=%s AND status='open'
                    ORDER BY due_on ASC""", (vet["id"],)) or []
        return jsonify({"followups": [_ser(r) for r in rows]})

    @app.route("/api/vet/followups/<int:fid>/answer", methods=["POST"])
    @vet_only
    def vet_answer_followup(vet, fid):
        d = request.get_json(silent=True) or {}
        row, why = answer_followup(q, q1, vet, fid, d.get("reply"))
        return (jsonify({"ok": True, "followup": _ser(row)}) if row
                else (jsonify({"error": why}), 403))

    @app.route("/api/vet/refills", methods=["GET"])
    @vet_only
    def vet_refills(vet):
        rows = q("""SELECT * FROM refill_requests WHERE vet_id=%s AND status='requested'
                    ORDER BY created_at ASC""", (vet["id"],)) or []
        return jsonify({"refills": [_ser(r) for r in rows]})

    @app.route("/api/vet/refills/<int:rid>/decide", methods=["POST"])
    @vet_only
    def vet_decide_refill(vet, rid):
        d = request.get_json(silent=True) or {}
        row, why = decide_refill(q, q1, vet, rid, bool(d.get("approve")),
                                 d.get("note") or "")
        return (jsonify({"ok": True, "refill": _ser(row)}) if row
                else (jsonify({"error": why}), 403))

    # -- owner side ----------------------------------------------------------
    def _uid():
        return session.get("user_id")

    @app.route("/api/care/plans", methods=["GET"])
    def care_plans():
        uid = _uid()
        if not uid:
            return jsonify({"error": "sign in"}), 401
        return jsonify({"plans": [_ser(p) for p in plans_for_owner(q, uid)]})

    @app.route("/api/care/plans/<int:plan_id>", methods=["GET"])
    def care_plan(plan_id):
        uid = _uid()
        data = get_plan(q, q1, plan_id)
        if not data:
            return jsonify({"error": "no such plan"}), 404
        if data["plan"].get("owner_user_id") != uid:
            return jsonify({"error": "not your plan"}), 403
        return jsonify(_ser(data))

    @app.route("/api/care/doses", methods=["GET"])
    def care_doses():
        uid = _uid()
        if not uid:
            return jsonify({"error": "sign in"}), 401
        return jsonify({"doses": [_ser(d) for d in due_doses(q, uid)]})

    @app.route("/api/care/doses/<int:dose_id>", methods=["POST"])
    def care_mark_dose(dose_id):
        uid = _uid()
        if not uid:
            return jsonify({"error": "sign in"}), 401
        d = request.get_json(silent=True) or {}
        own = q1("""SELECT p.owner_user_id FROM med_doses md
                    JOIN care_plan_items i ON i.id = md.item_id
                    JOIN care_plans p ON p.id = i.plan_id WHERE md.id=%s""", (dose_id,))
        if not own or own.get("owner_user_id") != uid:
            return jsonify({"error": "not your dose"}), 403
        return jsonify({"ok": True,
                        "dose": _ser(mark_dose(q, q1, dose_id, bool(d.get("given", True))))})

    @app.route("/api/care/followups/<int:fid>", methods=["POST"])
    def care_answer_followup(fid):
        uid = _uid()
        if not uid:
            return jsonify({"error": "sign in"}), 401
        d = request.get_json(silent=True) or {}
        row, why = open_followup(q, q1, fid, d.get("message"), d.get("photo_url"))
        return (jsonify({"ok": True, "followup": _ser(row)}) if row
                else (jsonify({"error": why}), 403))

    @app.route("/api/care/refills", methods=["POST"])
    def care_request_refill():
        uid = _uid()
        if not uid:
            return jsonify({"error": "sign in"}), 401
        d = request.get_json(silent=True) or {}
        rid, why = request_refill(q, q1, uid, d.get("pet_id"), d.get("medication"),
                                  d.get("item_id"), d.get("note") or "")
        if not rid:
            return jsonify({"error": why}), 400
        return jsonify({"ok": True, "refill_id": rid,
                        "note": "Sent to your vet. crittr routes the request — your "
                                "veterinarian decides and dispenses."})

    @app.route("/api/care/membership", methods=["GET"])
    def care_membership():
        uid = _uid()
        if not uid:
            return jsonify({"error": "sign in"}), 401
        active, row = is_member(q1, uid)
        return jsonify({"member": active, "tiers": CARE_TIERS,
                        "membership": _ser(row) if row else None})


def _ser(obj):
    """Rows contain dates and Decimals; make them JSON-safe without losing anything."""
    if obj is None:
        return None
    if isinstance(obj, list):
        return [_ser(o) for o in obj]
    if isinstance(obj, dict):
        return {k: (v if isinstance(v, (int, float, bool, type(None))) else str(v))
                for k, v in obj.items()}
    return str(obj)
