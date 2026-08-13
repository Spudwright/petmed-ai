"""crittr.ai — paying the practices, via Stripe Connect Express.

Until now the ledger knew exactly what a clinic was owed and had no way to send it. This
is the last piece. It is deliberately small, because moving money is the one part of this
system where a bug is not recoverable by editing a row.

WHY EXPRESS, AND NOT CUSTOM. With Express, Stripe hosts the onboarding flow and holds the
veterinarian's bank account and tax identity. crittr never sees them, never stores them and
cannot change them. That is not just less work — it is the security property that matters
most here: IF A VET'S CRITTR LOGIN IS COMPROMISED, THEIR PAYOUTS CANNOT BE REDIRECTED,
because the bank details live behind Stripe's own authentication rather than ours. A
homegrown "enter your bank details" form would make a crittr breach into a theft.

FIVE RULES, each protecting against a specific way this could lose real money:

  1. A VET CANNOT TRIGGER THEIR OWN PAYOUT. Creating transfers is admin-only. Otherwise a
     compromised vet login is a withdrawal button.
  2. EVERY TRANSFER CARRIES AN IDEMPOTENCY KEY derived from the payout id. Stripe retries,
     and networks fail after the money moved; without this a retry pays twice. The
     attribution ledger has this protection already — the transfer needs it more, because
     nothing here can be un-done by writing a negative row.
  3. NOTHING IS PAID UNTIL IT HAS SURVIVED THE REFUND WINDOW. Lines younger than
     CRITTR_PAYOUT_HOLDBACK_DAYS stay on the open statement. A refund on money we have
     already sent is a debt to chase; a refund on money we are still holding is arithmetic.
  4. WE PAY WHAT THE LEDGER SAYS, NEVER AN AMOUNT PASSED IN. The caller chooses a practice,
     not a number.
  5. A PAYOUT IS MARKED PAID ONLY AFTER STRIPE CONFIRMS. If the transfer throws, the lines
     stay unpaid and it can be retried — the same idempotency key makes that safe.

ONBOARDING LINKS ARE BEARER TOKENS. Anyone holding the URL can complete onboarding for that
account, so they are generated on demand for the signed-in vet, returned once, and never
emailed, stored or logged.
"""
import os
import logging

from flask import request, jsonify

import stripe

import vet_portal as vp
import vet_practice as vpr

log = logging.getLogger("crittr.payouts")

# How long a sale must sit before we will send the money. Most refunds arrive inside a
# couple of weeks, and money still on the open statement costs nothing to reverse.
HOLDBACK_DAYS = int(os.environ.get("CRITTR_PAYOUT_HOLDBACK_DAYS", "14"))
MIN_PAYOUT_CENTS = int(os.environ.get("CRITTR_MIN_PAYOUT_CENTS", "2500"))


def ensure_connect_schema(q):
    q("ALTER TABLE practices ADD COLUMN IF NOT EXISTS stripe_account_id TEXT", fetch=False)
    q("""ALTER TABLE practices
         ADD COLUMN IF NOT EXISTS payouts_enabled BOOLEAN DEFAULT FALSE""", fetch=False)
    q("ALTER TABLE practice_payouts ADD COLUMN IF NOT EXISTS transfer_id TEXT", fetch=False)
    q("ALTER TABLE practice_payouts ADD COLUMN IF NOT EXISTS failure TEXT", fetch=False)


def _app_url():
    return os.environ.get("APP_URL", "https://crittr.ai").rstrip("/")


# ── onboarding ───────────────────────────────────────────────────────────────

def ensure_account(q, q1, practice):
    """The practice's Stripe Express account, created on first use."""
    if practice.get("stripe_account_id"):
        return practice["stripe_account_id"], ""
    if not stripe.api_key:
        return None, "Stripe is not configured on this deployment"
    try:
        acct = stripe.Account.create(
            type="express",
            email=practice.get("contact_email") or None,
            business_type="company",
            company={"name": practice.get("name") or ""},
            capabilities={"transfers": {"requested": True}},
            metadata={"crittr_practice_id": str(practice["id"]),
                      "crittr_practice_name": practice.get("name") or ""},
        )
    except Exception as e:                                  # noqa: BLE001
        log.error("[payouts] could not create Express account for practice %s: %s",
                  practice["id"], e)
        return None, f"Stripe refused to create the account: {e}"
    q("UPDATE practices SET stripe_account_id=%s WHERE id=%s",
      (acct.id, practice["id"]), fetch=False)
    vp.audit(q, "practice_connect_account_created", actor="system",
             detail={"practice_id": practice["id"], "stripe_account_id": acct.id})
    return acct.id, ""


def onboarding_link(q, q1, practice):
    """A short-lived, single-use Stripe-hosted onboarding URL.

    Returned straight to the signed-in veterinarian and nowhere else. It is a bearer token:
    whoever holds it can finish onboarding for this account, so it is never emailed, never
    stored and never written to a log.
    """
    acct_id, why = ensure_account(q, q1, practice)
    if not acct_id:
        return None, why
    try:
        link = stripe.AccountLink.create(
            account=acct_id,
            refresh_url=f"{_app_url()}/vet/payouts?refresh=1",
            return_url=f"{_app_url()}/vet/payouts?done=1",
            type="account_onboarding",
        )
    except Exception as e:                                  # noqa: BLE001
        log.error("[payouts] account link failed for practice %s: %s", practice["id"], e)
        return None, f"could not start onboarding: {e}"
    return link.url, ""


def refresh_status(q, q1, practice):
    """Ask Stripe whether this account can actually receive money yet."""
    acct_id = practice.get("stripe_account_id")
    if not acct_id:
        return {"connected": False, "payouts_enabled": False,
                "next": "connect a bank account to receive your earnings"}
    try:
        acct = stripe.Account.retrieve(acct_id)
    except Exception as e:                                  # noqa: BLE001
        log.error("[payouts] could not read account %s: %s", acct_id, e)
        return {"connected": True, "payouts_enabled": False, "error": str(e)}
    enabled = bool(getattr(acct, "payouts_enabled", False))
    q("UPDATE practices SET payouts_enabled=%s WHERE id=%s",
      (enabled, practice["id"]), fetch=False)
    due = list(getattr(getattr(acct, "requirements", None), "currently_due", []) or [])
    return {"connected": True, "payouts_enabled": enabled,
            "requirements_due": due,
            "next": "" if enabled else
                    "Stripe still needs some details before it can pay you"}


# ── paying ───────────────────────────────────────────────────────────────────

def pay_practice(q, q1, practice_id, *, actor="admin", dry_run=False):
    """Close what has cleared the holdback, then transfer it. ADMIN ONLY (rule 1).

    Returns (result, reason). Everything that can refuse, refuses before any money moves.
    """
    practice = q1("SELECT * FROM practices WHERE id=%s", (practice_id,))
    if not practice:
        return None, "no such practice"
    if not practice.get("stripe_account_id"):
        return None, "this practice has not connected a bank account yet"
    if not practice.get("payouts_enabled"):
        # Re-check rather than trusting a stale flag — Stripe can disable an account.
        st = refresh_status(q, q1, practice)
        if not st.get("payouts_enabled"):
            return None, ("Stripe cannot pay this account yet — onboarding is incomplete")

    # Rule 3: only lines that have survived the refund window.
    ready = q1("""SELECT COALESCE(SUM(share_cents),0) AS cents, COUNT(*) AS n
                  FROM plan_attributions
                  WHERE practice_id=%s AND payout_id IS NULL
                    AND created_at < NOW() - (%s || ' days')::interval""",
               (practice_id, str(HOLDBACK_DAYS))) or {}
    amount = int(ready.get("cents") or 0)
    lines = int(ready.get("n") or 0)
    if lines == 0:
        return None, f"nothing has cleared the {HOLDBACK_DAYS}-day holdback yet"
    if amount <= 0:
        return None, (f"the balance is {amount}¢ — refunds have cancelled out these "
                      f"earnings, so there is nothing to send")
    if amount < MIN_PAYOUT_CENTS:
        return None, (f"${amount / 100:.2f} is below the ${MIN_PAYOUT_CENTS / 100:.2f} "
                      f"minimum — it will roll into the next payout")
    if dry_run:
        return {"would_pay_cents": amount, "lines": lines, "dry_run": True}, ""

    payout, why = vpr.close_statement(q, q1, practice_id,
                                      reference=f"auto:{actor}",
                                      holdback_days=HOLDBACK_DAYS)
    if not payout:
        return None, why

    # Rule 2 + 4: the amount comes from the ledger, and the key makes a retry safe.
    try:
        tr = stripe.Transfer.create(
            amount=int(payout["amount_cents"]),
            currency="usd",
            destination=practice["stripe_account_id"],
            description=f"crittr revenue share — payout #{payout['id']}",
            metadata={"crittr_payout_id": str(payout["id"]),
                      "crittr_practice_id": str(practice_id)},
            idempotency_key=f"crittr-payout-{payout['id']}",
        )
    except Exception as e:                                  # noqa: BLE001
        # Rule 5: leave it PENDING. The lines stay stamped to this payout, so retrying
        # with the same id reuses the same idempotency key and cannot double-pay.
        q("UPDATE practice_payouts SET failure=%s WHERE id=%s",
          (str(e)[:500], payout["id"]), fetch=False)
        log.error("[payouts] transfer failed for payout %s: %s", payout["id"], e)
        return None, f"the transfer failed and nothing was sent: {e}"

    vpr.mark_payout_paid(q, q1, payout["id"], reference=tr.id)
    q("UPDATE practice_payouts SET transfer_id=%s, failure=NULL WHERE id=%s",
      (tr.id, payout["id"]), fetch=False)
    vp.audit(q, "practice_paid", actor=actor,
             detail={"practice_id": practice_id, "payout_id": payout["id"],
                     "amount_cents": payout["amount_cents"], "transfer_id": tr.id})
    log.info("[payouts] practice %s paid %s¢ (payout %s, transfer %s)",
             practice_id, payout["amount_cents"], payout["id"], tr.id)
    return {"payout_id": payout["id"], "amount_cents": int(payout["amount_cents"]),
            "transfer_id": tr.id, "lines": lines}, ""


def retry_payout(q, q1, payout_id, *, actor="admin"):
    """Re-attempt a payout whose transfer failed. Safe: same idempotency key."""
    p = q1("SELECT * FROM practice_payouts WHERE id=%s", (payout_id,))
    if not p:
        return None, "no such payout"
    if p["status"] == "paid":
        return {"payout_id": payout_id, "already": True}, ""
    practice = q1("SELECT * FROM practices WHERE id=%s", (p["practice_id"],))
    try:
        tr = stripe.Transfer.create(
            amount=int(p["amount_cents"]), currency="usd",
            destination=practice["stripe_account_id"],
            description=f"crittr revenue share — payout #{payout_id}",
            metadata={"crittr_payout_id": str(payout_id)},
            idempotency_key=f"crittr-payout-{payout_id}",
        )
    except Exception as e:                                  # noqa: BLE001
        return None, f"still failing: {e}"
    vpr.mark_payout_paid(q, q1, payout_id, reference=tr.id)
    q("UPDATE practice_payouts SET transfer_id=%s, failure=NULL WHERE id=%s",
      (tr.id, payout_id), fetch=False)
    vp.audit(q, "practice_paid_retry", actor=actor,
             detail={"payout_id": payout_id, "transfer_id": tr.id})
    return {"payout_id": payout_id, "transfer_id": tr.id}, ""


# ── HTTP ─────────────────────────────────────────────────────────────────────

def register_payout_routes(app, q, q1, admin_required):
    vet_only = vp.require_vet(q1)

    @app.route("/api/vet/practice/connect", methods=["POST"])
    @vet_only
    def api_connect(vet):
        """Start onboarding. Returns a short-lived Stripe URL, once, to this vet only."""
        practice = vpr.practice_for_vet(q1, vet["id"])
        if not practice:
            return jsonify({"error": "create your practice first"}), 400
        url, why = onboarding_link(q, q1, practice)
        if not url:
            return jsonify({"error": why}), 400
        return jsonify({"ok": True, "onboarding_url": url,
                        "note": "this link is single-use and expires shortly — don't "
                                "forward it"})

    @app.route("/api/vet/practice/payouts", methods=["GET"])
    @vet_only
    def api_my_payouts(vet):
        practice = vpr.practice_for_vet(q1, vet["id"])
        if not practice:
            return jsonify({"error": "create your practice first"}), 400
        status = refresh_status(q, q1, practice)
        rows = q("""SELECT id, amount_cents, status, created_at, paid_at, transfer_id
                    FROM practice_payouts WHERE practice_id=%s
                    ORDER BY id DESC LIMIT 24""", (practice["id"],)) or []
        held = q1("""SELECT COALESCE(SUM(share_cents),0) AS cents
                     FROM plan_attributions
                     WHERE practice_id=%s AND payout_id IS NULL
                       AND created_at >= NOW() - (%s || ' days')::interval""",
                  (practice["id"], str(HOLDBACK_DAYS))) or {}
        return jsonify({
            "stripe": status,
            "open_statement": vpr.open_statement(q, q1, practice["id"]),
            "held_back_cents": int(held.get("cents") or 0),
            "holdback_days": HOLDBACK_DAYS,
            "minimum_payout_cents": MIN_PAYOUT_CENTS,
            "payouts": [vpr._ser(r) for r in rows],
        })

    # Rule 1: creating a transfer is ADMIN-ONLY. A vet can see what they are owed and
    # connect a bank account; they can never move money.

    @app.route("/api/admin/practices/<int:practice_id>/payout", methods=["POST"])
    @admin_required
    def api_pay_practice(practice_id):
        d = request.get_json(silent=True) or {}
        res, why = pay_practice(q, q1, practice_id,
                                actor=(d.get("actor") or "admin"),
                                dry_run=bool(d.get("dry_run")))
        if not res:
            return jsonify({"error": why}), 400
        return jsonify({"ok": True, **res})

    @app.route("/api/admin/payouts/<int:payout_id>/retry", methods=["POST"])
    @admin_required
    def api_retry_payout(payout_id):
        res, why = retry_payout(q, q1, payout_id)
        if not res:
            return jsonify({"error": why}), 400
        return jsonify({"ok": True, **res})

    @app.route("/api/admin/payouts/due", methods=["GET"])
    @admin_required
    def api_payouts_due():
        """Who is owed money that has cleared the holdback — the month-end worklist."""
        rows = q("""SELECT p.id, p.name, p.payouts_enabled,
                           p.stripe_account_id IS NOT NULL AS connected,
                           COALESCE(SUM(a.share_cents),0) AS cents
                    FROM practices p
                    LEFT JOIN plan_attributions a
                      ON a.practice_id = p.id AND a.payout_id IS NULL
                     AND a.created_at < NOW() - (%s || ' days')::interval
                    WHERE p.status='active'
                    GROUP BY p.id ORDER BY cents DESC""", (str(HOLDBACK_DAYS),)) or []
        return jsonify({"holdback_days": HOLDBACK_DAYS,
                        "minimum_cents": MIN_PAYOUT_CENTS,
                        "practices": [vpr._ser(r) for r in rows]})
