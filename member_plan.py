"""crittr.ai — the practice's share of a membership, paid every month it renews.

WHY THIS EXISTS. crittr's product revenue share pays a practice a percentage of what its
clients BUY. That lane earns a practice exactly nothing today, because every product in
the catalogue is an affiliate link and no crittr order is ever created. This is the other
lane, and the one the business actually runs on: the owner pays a monthly membership, and
the veterinarian who owns that client relationship earns a share of it for as long as the
client stays subscribed.

The distinction that matters is per-USE versus per-MONTH. Paying a vet per consult against
a flat monthly fee is unbounded — a member with a sick animal can consume more clinical
time in one month than they pay for in a year, and the people who buy an unlimited plan
are disproportionately the people who need it. Paying a share of the subscription instead
is capitation: crittr's cost per member is fixed, and the vet's income is predictable.

That trade moves the utilisation risk onto the veterinarian, which is why the membership
includes a BOUNDED number of video consults (see consults.member_allowance). A partner who
discovers their share buys unlimited access to their own time leaves, and a model without
veterinarians is not a model.

WHAT IT DOES NOT DO. It does not move money. Credits land on the practice's open statement
exactly like product credits, and connect_payouts.pay_practice is still the only thing that
pays anyone. It does not create a second earnings table either: a clinic wants ONE number,
and two ledgers eventually disagree about what that number is.

Env
---
    CRITTR_MEMBER_REV_SHARE_PCT   practice's share of each paid invoice. Default 50.
    CRITTR_MEMBER_CONSULTS_INCLUDED  video consults included per period. Default 2.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("crittr.member_plan")

# The practice's cut of every membership invoice. Start low and raise: a rate you lift is
# a good conversation with a partner, a rate you cut is the last one you have with them.
MEMBER_REV_SHARE_PCT = int(os.environ.get("CRITTR_MEMBER_REV_SHARE_PCT", "50"))

# Video consults included per billing period. Async messaging and AI triage are unbounded
# because they cost crittr fractions of a cent; a video consult costs a veterinarian
# twenty minutes, which is the scarce thing here.
CONSULTS_INCLUDED = int(os.environ.get("CRITTR_MEMBER_CONSULTS_INCLUDED", "2"))

SOURCE = "subscription"


def ensure_member_share_schema(q) -> None:
    """Widen the existing earnings ledger rather than starting a rival one."""
    try:
        q("ALTER TABLE plan_attributions ADD COLUMN IF NOT EXISTS stripe_invoice_id TEXT",
          fetch=False)
        # Stripe retries webhooks, and invoice.payment_succeeded can arrive more than once
        # for the same invoice. One POSITIVE credit per invoice, enforced by the database
        # rather than by the application remembering to check. Reversals are negative and
        # deliberately repeat the invoice id, so they are exempt.
        q("""CREATE UNIQUE INDEX IF NOT EXISTS idx_attr_one_credit_per_invoice
             ON plan_attributions(stripe_invoice_id)
             WHERE stripe_invoice_id IS NOT NULL AND share_cents > 0""", fetch=False)
    except Exception as e:                                          # noqa: BLE001
        log.warning("ensure_member_share_schema: %s", e)


def practice_for_owner(q1, user_id):
    """The practice that owns this client relationship, or None.

    One claimed practice per owner: vet_practice.claim() releases any previous link before
    setting a new one, so this cannot return two.
    """
    if not user_id:
        return None
    return q1("""SELECT p.*
                 FROM practice_clients c JOIN practices p ON p.id = c.practice_id
                 WHERE c.user_id = %s AND c.status = 'claimed'
                 ORDER BY c.claimed_at DESC LIMIT 1""", (user_id,))


def credit_subscription(q, q1, *, user_id, invoice_id, amount_cents, vet_id=None):
    """Credit a practice its share of one paid membership invoice.

    `amount_cents` must be what Stripe actually collected (amount_paid), not the list
    price — a coupon, proration or partial payment must not pay out more than came in.

    Silent no-op when the member has no claimed practice. That is the ordinary case for a
    member who found crittr through the triage chat rather than through a clinic, and it
    means crittr keeps the whole invoice, which is correct: nobody introduced them.
    """
    if not invoice_id or int(amount_cents or 0) <= 0:
        return None
    practice = practice_for_owner(q1, user_id)
    if not practice:
        log.info("[member_plan] invoice %s: member %s has no claimed practice, no credit",
                 invoice_id, user_id)
        return None
    pct = MEMBER_REV_SHARE_PCT
    share = int(round(int(amount_cents) * pct / 100.0))
    if share <= 0:
        return None
    q("""INSERT INTO plan_attributions
         (practice_id, vet_id, owner_user_id, amount_cents, share_pct, share_cents,
          source, stripe_invoice_id)
         VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
         ON CONFLICT DO NOTHING""",
      (practice["id"], vet_id, user_id, int(amount_cents), pct, share, SOURCE, invoice_id),
      fetch=False)
    log.info("[member_plan] invoice %s: practice %s credited %s¢ of %s¢ (%s%%)",
             invoice_id, practice["id"], share, amount_cents, pct)
    return share


def reverse_subscription(q, q1, *, invoice_id, reason="refund"):
    """Claw a membership credit back as a NEGATIVE row, never by editing the original.

    A statement has to show the credit AND its reversal, or a practice cannot reconcile
    what it was told last month against what it is being paid this month.
    """
    if not invoice_id:
        return None
    orig = q1("""SELECT * FROM plan_attributions
                 WHERE stripe_invoice_id=%s AND share_cents > 0
                 ORDER BY id LIMIT 1""", (invoice_id,))
    if not orig:
        return None
    already = q1("""SELECT COALESCE(SUM(share_cents),0) AS s FROM plan_attributions
                    WHERE stripe_invoice_id=%s AND share_cents < 0""", (invoice_id,))
    if abs(int((already or {}).get("s") or 0)) >= int(orig["share_cents"]):
        return None                      # already reversed; Stripe retried
    q("""INSERT INTO plan_attributions
         (practice_id, vet_id, owner_user_id, amount_cents, share_pct, share_cents,
          source, stripe_invoice_id)
         VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
      (orig["practice_id"], orig.get("vet_id"), orig.get("owner_user_id"),
       -int(orig["amount_cents"]), orig["share_pct"], -int(orig["share_cents"]),
       SOURCE + "_reversal", invoice_id), fetch=False)
    log.info("[member_plan] invoice %s reversed (%s): practice %s debited %s¢",
             invoice_id, reason, orig["practice_id"], orig["share_cents"])
    return -int(orig["share_cents"])
