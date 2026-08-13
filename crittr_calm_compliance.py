"""crittr.ai — bring CRITTR Calm's copy inside the lines before it becomes a real product.

WHY THIS IS A SEPARATE FILE. crittr_calm_seed is idempotent: once the product exists it
returns early and never touches it again. That is the right behaviour for a seed — it must
not clobber hand-edits — but it means changing the seed's description does nothing to the
row already live. This is the migration that does.

THREE PROBLEMS WITH THE ORIGINAL COPY, in order of how much they would cost:

  1. HEMP. AAFCO has not accepted hemp as an approved feed ingredient, FDA has not approved
     hemp derivatives for animals, and several states restrict them in pet products. It is
     the single ingredient that turns a routine state feed registration into an argument.
     The product does not physically exist yet, so this costs nothing to remove now and
     would cost a great deal to remove after a production run.
  2. "VETERINARIAN-FORMULATED." A claim that has to be literally true. No veterinarian has
     formulated anything yet, so it is currently false advertising on a live page. It can
     go back the day one does — and for a product sold into the veterinary channel, hiring
     that formulator is worth doing anyway.
  3. "ANXIETY AND STRESS SUPPORT." Claiming to treat, prevent or mitigate a condition makes
     a supplement an unapproved DRUG under the FDCA. "Anxiety" reads as a condition;
     "supports calm behaviour during travel and loud noises" is a structure/function claim
     about a situation. Same product, different legal category.

WHAT THIS DOES NOT DO. It does not decide whether the product is sellable. It is currently
in_stock with no supplier and no fulfilment behind it, which is a separate and worse
problem — see set_stock() below, which is deliberately not called automatically.
"""
import logging

log = logging.getLogger("crittr.calm_compliance")

_SLUG = "crittr-calm"
_MARKER = "crittr_calm_compliance_v1:applied"

# Situational, not conditional. Describes when you'd give it, not what it treats.
DESCRIPTION = (
    "Calming soft chews for dogs. Supports calm, relaxed behaviour with L-theanine, "
    "chamomile and valerian root. Chicken flavor. Made for fireworks, thunderstorms, "
    "travel, and vet visits. 60 soft chews per jar."
)
WARNINGS = (
    "For supplemental feeding only. Consult your veterinarian before use if your dog is "
    "pregnant, nursing, or on other medications. Keep out of reach of children."
)
# 'anxiety' removed from the tag list too — tags feed search and the AI recommender, so
# leaving it there reintroduces the condition claim through a side door.
TAGS = "supplement,calming,otc,crittr-brand"


def _applied(q1):
    try:
        return bool(q1("SELECT 1 AS x FROM crittr_meta WHERE key=%s", (_MARKER,)))
    except Exception:
        return False


def ensure_calm_compliance(q, q1):
    """Rewrite the live CRITTR Calm copy. Idempotent; safe to call on every boot."""
    if _applied(q1):
        return {"applied": False, "reason": "already applied"}
    row = q1("SELECT id, description FROM products WHERE slug=%s", (_SLUG,))
    if not row:
        return {"applied": False, "reason": "CRITTR Calm not present"}
    before = row.get("description") or ""
    try:
        q("""UPDATE products SET description=%s, warnings=%s, tags=%s WHERE id=%s""",
          (DESCRIPTION, WARNINGS, TAGS, row["id"]), fetch=False)
        q("""INSERT INTO crittr_meta(key, value) VALUES (%s,%s)
             ON CONFLICT (key) DO NOTHING""", (_MARKER, "applied"), fetch=False)
    except Exception as e:                                  # noqa: BLE001
        log.warning("[calm_compliance] update failed: %s", e)
        return {"applied": False, "reason": str(e)[:120]}
    log.info("[calm_compliance] rewrote CRITTR Calm copy (hemp / vet-formulated / "
             "condition claim removed)")
    return {"applied": True, "removed": [w for w in ("hemp", "Veterinarian-formulated",
                                                     "anxiety") if w.lower() in before.lower()]}


def set_stock(q, in_stock):
    """Take CRITTR Calm off sale, or put it back.

    NOT called automatically, because it is a commercial decision rather than a compliance
    one. But the position to be aware of: the product has no amazon_url, so the shop renders
    "Add to cart" and a customer can pay $29.99 today for something with no supplier, no
    stock and no fulfilment path behind it. That is the same condition that got four fake Rx
    products deleted in Phase H.10.
    """
    q("UPDATE products SET in_stock=%s WHERE slug=%s", (bool(in_stock), _SLUG), fetch=False)
    return {"slug": _SLUG, "in_stock": bool(in_stock)}
