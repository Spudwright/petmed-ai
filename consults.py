"""crittr.ai — paid telehealth consults, and crittr's cut of them.

THE MODEL THIS IMPLEMENTS. crittr is free to use. The owner pays only when a veterinarian
actually does something, and crittr takes a percentage of that. No subscription, no
inventory, no stock, no cash outlay — the first revenue stream that costs nothing to run.

WHY THIS IS THE RIGHT SHAPE FOR PETS. A membership asks an owner to pay every month for
something they need twice a year, and they say no. A $55 charge at 2am when the dog is
unwell and their own vet answers is an easy yes. The willingness to pay is concentrated in
the moment of worry, so the price should be too.

THE MONEY NEVER TOUCHES CRITTR. This uses Stripe destination charges: the client pays, the
funds settle into the PRACTICE'S connected account, and crittr's percentage is retained as
an application fee at the moment of payment. The account is the practice's rather than the
individual vet's because that is the entity with the bank details and the tax identity —
and it means product revenue share and consult fees land in one place, so a vet onboards
once. That matters for three reasons —

  1. No payout run, no invoicing, no chasing. The split happens once, automatically.
  2. crittr never holds a clinician's fee, which is a very different regulatory posture
     from being a platform that collects and later distributes money for medical services.
  3. `on_behalf_of` makes the vet the settlement merchant, so the consult is legibly THEIR
     service that crittr took a commission on — not crittr selling veterinary care.

That third one is the important one. crittr does not practise veterinary medicine and must
not look like it does in a payments ledger either.

FOUR REFUSALS, all before money moves:
  * an unverified vet cannot charge
  * a vet whose practice has no connected Stripe account cannot charge
  * a vet cannot charge in a state they are not licensed and activated in
  * a consult cannot be paid for twice
"""
import os
import logging
from datetime import datetime, timezone

from flask import request, jsonify, session

import stripe

import vet_portal as vp
import vet_compliance as vc

log = logging.getLogger("crittr.consults")

# crittr's share of a consult. Deliberately modest: the veterinarian does the clinical work
# and carries the licence; crittr brings the client, the record and the rails. A rate a
# practice resents is one they route around, and you can raise it later far more easily
# than you can lower it.
PLATFORM_FEE_PCT = int(os.environ.get("CRITTR_CONSULT_FEE_PCT", "20"))

DEFAULT_FEE_CENTS = int(os.environ.get("CRITTR_DEFAULT_CONSULT_CENTS", "5500"))
MIN_FEE_CENTS = 1500
MAX_FEE_CENTS = 50000

QUOTED = "quoted"        # offered to the owner, not yet paid
PAID = "paid"            # money split; the vet owes a consult
COMPLETED = "completed"  # the vet has done it
REFUNDED = "refunded"
CANCELLED = "cancelled"


def init_consult_tables(q):
    q("""
    CREATE TABLE IF NOT EXISTS consult_fees (
        vet_id          INTEGER PRIMARY KEY REFERENCES vets(id) ON DELETE CASCADE,
        amount_cents    INTEGER NOT NULL,
        blurb           TEXT,
        updated_at      TIMESTAMPTZ DEFAULT NOW()
    )""", fetch=False)
    q("""
    CREATE TABLE IF NOT EXISTS consults (
        id              SERIAL PRIMARY KEY,
        vet_id          INTEGER NOT NULL REFERENCES vets(id) ON DELETE CASCADE,
        owner_user_id   INTEGER NOT NULL,
        pet_id          INTEGER,
        case_id         INTEGER,
        state           CHAR(2),
        reason          TEXT,
        -- Frozen at quote time. A vet who raises their fee must not silently reprice a
        -- consult an owner is halfway through paying for.
        amount_cents        INTEGER NOT NULL,
        platform_fee_pct    INTEGER NOT NULL,
        platform_fee_cents  INTEGER NOT NULL,
        vet_amount_cents    INTEGER NOT NULL,
        status          TEXT NOT NULL DEFAULT 'quoted',
        stripe_session_id   TEXT,
        stripe_payment_intent TEXT,
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        paid_at         TIMESTAMPTZ,
        completed_at    TIMESTAMPTZ,
        refunded_at     TIMESTAMPTZ
    )""", fetch=False)
    # A consult the membership paid for. The vet's money for it arrived through their
    # share of the subscription, so no Stripe charge exists and none should be created.
    q("ALTER TABLE consults ADD COLUMN IF NOT EXISTS covered BOOLEAN DEFAULT FALSE",
      fetch=False)
    q("""CREATE INDEX IF NOT EXISTS idx_consults_owner ON consults(owner_user_id, status)""",
      fetch=False)
    q("""CREATE INDEX IF NOT EXISTS idx_consults_vet ON consults(vet_id, status)""",
      fetch=False)


def fee_for(q1, vet_id):
    row = q1("SELECT * FROM consult_fees WHERE vet_id=%s", (vet_id,))
    return int(row["amount_cents"]) if row else DEFAULT_FEE_CENTS


def set_fee(q, q1, vet_id, amount_cents, blurb=None):
    try:
        amount_cents = int(amount_cents)
    except (TypeError, ValueError):
        return None, "the fee must be a whole number of cents"
    if not (MIN_FEE_CENTS <= amount_cents <= MAX_FEE_CENTS):
        return None, (f"a consult fee must be between ${MIN_FEE_CENTS/100:.0f} and "
                      f"${MAX_FEE_CENTS/100:.0f}")
    q("""INSERT INTO consult_fees (vet_id, amount_cents, blurb) VALUES (%s,%s,%s)
         ON CONFLICT (vet_id) DO UPDATE SET amount_cents=EXCLUDED.amount_cents,
             blurb=EXCLUDED.blurb, updated_at=NOW()""",
      (vet_id, amount_cents, blurb), fetch=False)
    return q1("SELECT * FROM consult_fees WHERE vet_id=%s", (vet_id,)), ""


def _split(amount_cents):
    fee = int(round(int(amount_cents) * PLATFORM_FEE_PCT / 100.0))
    return fee, int(amount_cents) - fee


# ── offering a consult ───────────────────────────────────────────────────────

def payee_account(q1, vet_id):
    """The Stripe account a consult settles into: the PRACTICE's, not the vet's.

    Connect onboarding is per practice, because the practice is the entity with the bank
    account and the tax identity. Both income streams — product revenue share and consult
    fees — land in the same place, so a vet onboards once rather than twice.
    """
    import vet_practice as vpr
    p = vpr.practice_for_vet(q1, vet_id)
    if not p:
        return None, "set up your practice first — that's where payment is sent"
    if not p.get("stripe_account_id"):
        return None, ("connect a bank account before charging for consults — payment goes "
                      "to your practice, not to crittr, so we need somewhere to send it")
    if not p.get("payouts_enabled"):
        return None, "Stripe has not finished verifying your practice's account yet"
    return p["stripe_account_id"], ""


def member_allowance(q1, owner_user_id):
    """(used, included) video consults for this member in the current billing period.

    Counted from the membership's start day rather than the calendar month, because an
    owner who joined on the 20th should not get a fresh allowance eleven days later.
    Cancelled and refunded consults do not count against it; a consult that never
    happened should not consume an allowance.
    """
    import member_plan as mp
    m = q1("""SELECT started_at FROM care_members
              WHERE user_id=%s AND status='active'
                AND (ends_at IS NULL OR ends_at > NOW())""", (owner_user_id,))
    if not m:
        return 0, 0
    row = q1("""SELECT COUNT(*) AS n FROM consults
                WHERE owner_user_id=%s AND covered
                  AND status <> %s AND status <> %s
                  AND created_at > NOW() - INTERVAL '1 month'""",
             (owner_user_id, CANCELLED, REFUNDED))
    return int((row or {}).get("n") or 0), mp.CONSULTS_INCLUDED


def may_charge(q, q1, vet, state):
    """Everything that must be true before a vet can take an owner's money."""
    if vet.get("status") != vp.VET_STATUS_VERIFIED:
        return False, "your account is not verified yet"
    acct, why = payee_account(q1, vet["id"])
    if not acct:
        return False, why
    st = (state or "").upper()[:2]
    if st and st not in vp.active_states_for_vet(q, q1, vet["id"]):
        return False, (f"you hold no verified, unexpired licence in {st}, or crittr is not "
                       f"yet permitted to operate there")
    return True, ""


def quote(q, q1, vet, *, owner_user_id, pet_id=None, state=None, reason="", case_id=None,
          amount_cents=None):
    """Offer a paid consult to an owner. Nothing is charged here."""
    ok, why = may_charge(q, q1, vet, state)
    if not ok:
        return None, why
    if not owner_user_id:
        return None, "no owner specified"
    # A member inside their monthly allowance pays nothing, and no Stripe session is
    # ever created for it. The veterinarian has already been paid for this consult
    # through their share of the subscription — charging again, or splitting a second
    # time, would pay them twice for one piece of work.
    used, included = member_allowance(q1, owner_user_id)
    covered = bool(included) and used < included
    if covered:
        row = q1("""INSERT INTO consults
                    (vet_id, owner_user_id, pet_id, case_id, state, reason, amount_cents,
                     platform_fee_pct, platform_fee_cents, vet_amount_cents, status,
                     covered, paid_at)
                    VALUES (%s,%s,%s,%s,%s,%s,0,0,0,0,'paid',TRUE,NOW()) RETURNING *""",
                 (vet["id"], owner_user_id, pet_id, case_id,
                  (state or "").upper()[:2] or None, (reason or "").strip()[:500]))
        vp.audit(q, "consult_covered", actor=vet.get("email", ""), vet_id=vet["id"],
                 case_id=case_id,
                 detail={"consult_id": (row or {}).get("id"),
                         "used": used + 1, "included": included})
        return row, ""
    amount = int(amount_cents or fee_for(q1, vet["id"]))
    if not (MIN_FEE_CENTS <= amount <= MAX_FEE_CENTS):
        return None, "that fee is outside the permitted range"
    fee, vet_amount = _split(amount)
    row = q1("""INSERT INTO consults
                (vet_id, owner_user_id, pet_id, case_id, state, reason, amount_cents,
                 platform_fee_pct, platform_fee_cents, vet_amount_cents, status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'quoted') RETURNING *""",
             (vet["id"], owner_user_id, pet_id, case_id, (state or "").upper()[:2] or None,
              (reason or "").strip()[:500], amount, PLATFORM_FEE_PCT, fee, vet_amount))
    vp.audit(q, "consult_quoted", actor=vet.get("email", ""), vet_id=vet["id"],
             case_id=case_id,
             detail={"consult_id": (row or {}).get("id"), "amount_cents": amount,
                     "platform_fee_cents": fee})
    return row, ""


# ── paying for it ────────────────────────────────────────────────────────────

def checkout_url(q, q1, consult_id, owner_user_id, app_url):
    """A Stripe Checkout session that pays the VET and retains crittr's fee.

    Destination charge + on_behalf_of: the funds settle to the veterinarian's account and
    the consult is legibly their service. crittr takes a commission; it does not sell
    veterinary care.
    """
    c = q1("""SELECT c.*, v.full_name
              FROM consults c JOIN vets v ON v.id=c.vet_id WHERE c.id=%s""", (consult_id,))
    if not c:
        return None, "no such consult"
    if c["owner_user_id"] != owner_user_id:
        return None, "that consult is not yours"
    if c.get("covered"):
        return None, "this consult is included in your membership — nothing to pay"
    if c["status"] == PAID:
        return None, "this consult is already paid for"
    if c["status"] not in (QUOTED,):
        return None, f"this consult is '{c['status']}'"
    destination, why = payee_account(q1, c["vet_id"])
    if not destination:
        return None, f"this veterinarian cannot accept payment yet — {why}"
    if not stripe.api_key:
        return None, "payments are not configured on this deployment"
    try:
        s = stripe.checkout.Session.create(
            mode="payment",
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "unit_amount": int(c["amount_cents"]),
                    "product_data": {
                        "name": f"Video consult with {c['full_name']}",
                        "description": (c.get("reason") or
                                        "Telehealth consultation")[:300],
                    },
                },
                "quantity": 1,
            }],
            payment_intent_data={
                # crittr's cut, retained at the moment of payment.
                "application_fee_amount": int(c["platform_fee_cents"]),
                "transfer_data": {"destination": destination},
                # The vet is the settlement merchant: their service, their liability,
                # crittr's commission.
                "on_behalf_of": destination,
                "metadata": {"crittr_consult_id": str(consult_id)},
            },
            metadata={"flow": "consult", "crittr_consult_id": str(consult_id)},
            success_url=f"{app_url}/consult/{consult_id}?paid=1",
            cancel_url=f"{app_url}/consult/{consult_id}?canceled=1",
        )
    except Exception as e:                                  # noqa: BLE001
        log.error("[consults] checkout failed for consult %s: %s", consult_id, e)
        return None, f"could not start payment: {e}"
    q("UPDATE consults SET stripe_session_id=%s WHERE id=%s", (s.id, consult_id),
      fetch=False)
    return s.url, ""


def mark_paid(q, q1, consult_id, payment_intent=None):
    """Called from the webhook. Idempotent — Stripe retries."""
    c = q1("SELECT * FROM consults WHERE id=%s", (consult_id,))
    if not c:
        return None
    if c["status"] == PAID:
        return c
    q("""UPDATE consults SET status='paid', paid_at=NOW(), stripe_payment_intent=%s
         WHERE id=%s AND status <> 'paid'""", (payment_intent, consult_id), fetch=False)
    vp.audit(q, "consult_paid", actor=str(c["owner_user_id"]), vet_id=c["vet_id"],
             detail={"consult_id": consult_id, "amount_cents": c["amount_cents"],
                     "platform_fee_cents": c["platform_fee_cents"]})
    log.info("[consults] consult %s paid — vet %s¢, crittr %s¢",
             consult_id, c["vet_amount_cents"], c["platform_fee_cents"])
    return q1("SELECT * FROM consults WHERE id=%s", (consult_id,))


def complete(q, q1, vet, consult_id, notes=""):
    c = q1("SELECT * FROM consults WHERE id=%s", (consult_id,))
    if not c:
        return None, "no such consult"
    if c["vet_id"] != vet["id"]:
        return None, "that consult is not yours"
    if c["status"] != PAID:
        return None, f"that consult is '{c['status']}', not paid"
    q("UPDATE consults SET status='completed', completed_at=NOW() WHERE id=%s",
      (consult_id,), fetch=False)
    vp.audit(q, "consult_completed", actor=vet.get("email", ""), vet_id=vet["id"],
             detail={"consult_id": consult_id, "notes": (notes or "")[:300]})
    return q1("SELECT * FROM consults WHERE id=%s", (consult_id,)), ""


def refund(q, q1, consult_id, reason=""):
    """Refund a consult that did not happen — and give back crittr's cut too.

    `refund_application_fee` is not optional in spirit: keeping a commission on a service
    that was never delivered is the kind of thing a practice tells other practices about.
    """
    c = q1("SELECT * FROM consults WHERE id=%s", (consult_id,))
    if not c:
        return None, "no such consult"
    if c["status"] not in (PAID, COMPLETED):
        return None, f"nothing to refund — that consult is '{c['status']}'"
    if not c.get("stripe_payment_intent"):
        return None, "no payment on record for that consult"
    try:
        stripe.Refund.create(
            payment_intent=c["stripe_payment_intent"],
            refund_application_fee=True,
            reverse_transfer=True,
            reason="requested_by_customer",
        )
    except Exception as e:                                  # noqa: BLE001
        log.error("[consults] refund failed for consult %s: %s", consult_id, e)
        return None, f"refund failed: {e}"
    q("UPDATE consults SET status='refunded', refunded_at=NOW() WHERE id=%s",
      (consult_id,), fetch=False)
    vp.audit(q, "consult_refunded", actor="admin", vet_id=c["vet_id"],
             detail={"consult_id": consult_id, "reason": reason[:200]})
    return q1("SELECT * FROM consults WHERE id=%s", (consult_id,)), ""


def vet_earnings(q, q1, vet_id, days=30):
    row = q1("""SELECT COALESCE(SUM(vet_amount_cents),0) AS cents, COUNT(*) AS n
                FROM consults WHERE vet_id=%s AND status IN ('paid','completed')
                  AND paid_at > NOW() - (%s || ' days')::interval""",
             (vet_id, str(int(days))))
    return {"days": days, "consults": int((row or {}).get("n") or 0),
            "earned_cents": int((row or {}).get("cents") or 0)}


def platform_revenue(q, q1, days=30):
    row = q1("""SELECT COALESCE(SUM(platform_fee_cents),0) AS cents, COUNT(*) AS n
                FROM consults WHERE status IN ('paid','completed')
                  AND paid_at > NOW() - (%s || ' days')::interval""", (str(int(days)),))
    return {"days": days, "consults": int((row or {}).get("n") or 0),
            "revenue_cents": int((row or {}).get("cents") or 0),
            "fee_pct": PLATFORM_FEE_PCT}


# ── HTTP ─────────────────────────────────────────────────────────────────────

def register_consult_routes(app, q, q1, admin_required):
    vet_only = vp.require_vet(q1)
    app_url = os.environ.get("APP_URL", "https://crittr.ai").rstrip("/")

    @app.route("/api/vet/consult-fee", methods=["GET", "POST"])
    @vet_only
    def api_consult_fee(vet):
        if request.method == "POST":
            d = request.get_json(silent=True) or {}
            row, why = set_fee(q, q1, vet["id"], d.get("amount_cents"), d.get("blurb"))
            if not row:
                return jsonify({"error": why}), 400
            fee, vet_amount = _split(int(row["amount_cents"]))
            return jsonify({"ok": True, "amount_cents": int(row["amount_cents"]),
                            "you_receive_cents": vet_amount,
                            "crittr_fee_cents": fee, "crittr_fee_pct": PLATFORM_FEE_PCT})
        amount = fee_for(q1, vet["id"])
        fee, vet_amount = _split(amount)
        return jsonify({"amount_cents": amount, "you_receive_cents": vet_amount,
                        "crittr_fee_cents": fee, "crittr_fee_pct": PLATFORM_FEE_PCT,
                        "can_charge": may_charge(q, q1, vet, None)[0]})

    @app.route("/api/vet/consults", methods=["GET", "POST"])
    @vet_only
    def api_vet_consults(vet):
        if request.method == "POST":
            d = request.get_json(silent=True) or {}
            row, why = quote(q, q1, vet, owner_user_id=d.get("owner_user_id"),
                             pet_id=d.get("pet_id"), state=d.get("state"),
                             reason=d.get("reason"), case_id=d.get("case_id"),
                             amount_cents=d.get("amount_cents"))
            if not row:
                return jsonify({"error": why}), 400
            return jsonify({"ok": True, "consult": _ser(row),
                            "pay_url": f"{app_url}/consult/{row['id']}"})
        rows = q("""SELECT * FROM consults WHERE vet_id=%s ORDER BY created_at DESC
                    LIMIT 100""", (vet["id"],)) or []
        return jsonify({"consults": [_ser(r) for r in rows],
                        "earnings": vet_earnings(q, q1, vet["id"])})

    @app.route("/api/vet/consults/<int:cid>/complete", methods=["POST"])
    @vet_only
    def api_complete_consult(vet, cid):
        d = request.get_json(silent=True) or {}
        row, why = complete(q, q1, vet, cid, d.get("notes") or "")
        if not row:
            return jsonify({"error": why}), 400
        return jsonify({"ok": True, "consult": _ser(row)})

    # ── the owner's side ─────────────────────────────────────────────────────

    @app.route("/api/consults", methods=["GET"])
    def api_my_consults():
        uid = session.get("user_id")
        if not uid:
            return jsonify({"error": "sign in first"}), 401
        rows = q("""SELECT c.*, v.full_name AS vet_name, v.clinic_name
                    FROM consults c JOIN vets v ON v.id=c.vet_id
                    WHERE c.owner_user_id=%s ORDER BY c.created_at DESC LIMIT 50""",
                 (uid,)) or []
        return jsonify({"consults": [_ser(r) for r in rows]})

    @app.route("/api/consults/<int:cid>/pay", methods=["POST"])
    def api_pay_consult(cid):
        uid = session.get("user_id")
        if not uid:
            return jsonify({"error": "sign in first", "needs_auth": True}), 401
        url, why = checkout_url(q, q1, cid, uid, app_url)
        if not url:
            return jsonify({"error": why}), 400
        return jsonify({"ok": True, "url": url})

    @app.route("/consult/<int:cid>", methods=["GET"])
    def consult_page(cid):
        c = q1("""SELECT c.*, v.full_name AS vet_name, v.clinic_name
                  FROM consults c JOIN vets v ON v.id=c.vet_id WHERE c.id=%s""", (cid,))
        if not c:
            return _page("<h1>That consult link isn't valid</h1>"
                         "<p class=sub>Ask your veterinary practice to send a new one.</p>"), 404
        return _consult_page(c)

    # ── admin ────────────────────────────────────────────────────────────────

    @app.route("/api/admin/consults/revenue", methods=["GET"])
    @admin_required
    def api_consult_revenue():
        days = int(request.args.get("days") or 30)
        return jsonify(platform_revenue(q, q1, days))

    @app.route("/api/admin/consults/<int:cid>/refund", methods=["POST"])
    @admin_required
    def api_refund_consult(cid):
        d = request.get_json(silent=True) or {}
        row, why = refund(q, q1, cid, d.get("reason") or "")
        if not row:
            return jsonify({"error": why}), 400
        return jsonify({"ok": True, "consult": _ser(row)})


_CSS = """<style>
 :root{--ink:#1C2A1F;--mut:#6E7D70;--line:#DFE5DB;--sage:#527E54;--sage-d:#3E6340;--cream:#FDFBF5}
 *{box-sizing:border-box}
 body{margin:0;background:var(--cream);color:var(--ink);
      font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
 .wrap{max-width:520px;margin:0 auto;padding:44px 20px}
 .brand{font-weight:800;font-size:22px;color:var(--sage-d);text-decoration:none;
        display:inline-block;margin-bottom:28px}
 h1{font-size:28px;line-height:1.2;margin:0 0 10px;letter-spacing:-.02em}
 .sub{color:var(--mut);margin:0 0 24px}
 .card{background:#fff;border:1px solid var(--line);border-radius:14px;padding:24px}
 .amt{font-size:40px;font-weight:800;letter-spacing:-.03em;margin:6px 0}
 button{background:var(--sage);color:#fff;border:0;border-radius:10px;padding:15px 24px;
        font:inherit;font-weight:700;cursor:pointer;width:100%;margin-top:18px}
 button:hover{background:var(--sage-d)}
 .meta{color:var(--mut);font-size:14px}
 .ok{background:#EAF5E9;border:1px solid #A6C9A2;color:#2D4A30;padding:14px;
     border-radius:10px;margin-top:16px}
</style>"""


def _page(body):
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>Consult · crittr</title>{_CSS}</head><body><div class=wrap>"
            f"<a class=brand href='/'>crittr</a>{body}</div></body></html>")


def _consult_page(c):
    paid = c["status"] in (PAID, COMPLETED)
    who = c.get("clinic_name") or c.get("vet_name") or "your veterinarian"
    if paid:
        return _page(
            f"<h1>You're booked in with {c.get('vet_name')}</h1>"
            f"<p class=sub>Payment received. {who} will be in touch to arrange a time.</p>"
            f"<div class=card><div class=meta>Consult</div>"
            f"<div class=amt>${c['amount_cents']/100:,.2f}</div>"
            f"<div class=ok>Paid. Nothing more to do.</div></div>")
    return _page(
        f"<h1>Video consult with {c.get('vet_name')}</h1>"
        f"<p class=sub>{c.get('reason') or 'A follow-up about your pet.'}</p>"
        f"<div class=card>"
        f"<div class=meta>{who}</div>"
        f"<div class=amt>${c['amount_cents']/100:,.2f}</div>"
        f"<div class=meta>Paid directly to your veterinary practice. You're only charged "
        f"once, for this consult — there's no subscription.</div>"
        f"<button id=pay>Pay and book</button>"
        f"<div id=msg class=meta style='margin-top:12px'></div></div>"
        f"<script>document.getElementById('pay').onclick=async()=>{{"
        f" const m=document.getElementById('msg'); m.textContent='Opening payment…';"
        f" const r=await fetch('/api/consults/{c['id']}/pay',{{method:'POST'}});"
        f" const j=await r.json();"
        f" if(j.url){{location.href=j.url;}}"
        f" else if(j.needs_auth){{location.href='/login?next=/consult/{c['id']}';}}"
        f" else{{m.textContent=j.error||'Could not start payment';}}}};</script>")


def _ser(obj):
    if obj is None:
        return None
    return {k: (v if isinstance(v, (int, float, bool, type(None))) else str(v))
            for k, v in dict(obj).items()}
