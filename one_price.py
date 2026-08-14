"""crittr.ai — one price, shipping included. And the second own-label SKU, listed but off sale.

WHY ONE PRICE. Checkout used to add a flat $5.99 postage unless the basket cleared $50.
That is three things for a customer to work out on a $29.99 jar, and it converts worse than
a single number. Postage on a 1lb jar runs about $7 and varies by roughly $3 between the
nearest and furthest zone; averaging that into the ticket price costs less than the
friction did. Sales tax is NOT baked in — that is Stripe Tax's job now, added at checkout
per destination, because absorbing a rate that swings from zero to over ten percent would
quietly eat the margin in exactly the states you sell most in.

THE CUSTOMER PAYS SLIGHTLY LESS. CRITTR Calm was $29.99 + $5.99 = $35.98 at the till. It is
now $34.99 flat. crittr gives up about a dollar of net per jar and buys a simpler checkout
with it.

CRITTR JOINT IS LISTED BUT NOT SELLABLE. It is the recommended first manufactured SKU —
the highest-priced category in the affiliate catalogue, the most common thing a vet
actually recommends, and the easiest format to co-pack. Listing it now means the plumbing,
imagery and copy are ready when stock lands. It is inserted with in_stock FALSE and no
supplier, so dropship.fulfillable() refuses it and it cannot be bought. The moment a
supplier is linked it becomes sellable, and not one moment before.

Copy is written to the same line as the CRITTR Calm rewrite: structure/function only.
"Supports joint mobility" is a supplement. "Relieves arthritis pain" is an unapproved drug.
"""
import logging

log = logging.getLogger("crittr.one_price")

_MARKER = "crittr_one_price_v1:applied"

# Ticket price now includes postage. Was $29.99 + $5.99 shipping.
CALM_PRICE_CENTS = 3499
CALM_COMPARE_CENTS = 3999

JOINT = {
    "slug": "crittr-joint",
    "name": "CRITTR Joint",
    "price_cents": 3999,          # shipping included
    "compare_cents": 4499,
    "species": "dog",
    "description": (
        "Hip and joint soft chews for dogs. Supports joint mobility, cartilage and "
        "connective tissue with glucosamine, MSM, chondroitin and green-lipped mussel. "
        "Beef flavor. For active and senior dogs. 60 soft chews per jar."
    ),
    "dosage": (
        "Up to 25 lbs: 1 chew daily. 26-50 lbs: 2 chews. 51-100 lbs: 3 chews. "
        "Over 100 lbs: 4 chews. Allow 4-6 weeks of daily use."
    ),
    "warnings": (
        "For supplemental feeding only. For use in dogs only. Consult your veterinarian "
        "before use if your dog is pregnant, nursing, or on other medication. Keep out of "
        "reach of children and animals."
    ),
    "tags": "supplement,joint,mobility,otc,crittr-brand",
}


def _applied(q1):
    try:
        return bool(q1("SELECT 1 AS x FROM crittr_meta WHERE key=%s", (_MARKER,)))
    except Exception:
        return False


def ensure_one_price(q, q1):
    """Re-price for shipping-inclusive, and list CRITTR Joint off sale. Idempotent."""
    if _applied(q1):
        return {"applied": False, "reason": "already applied"}
    out = {"applied": True, "repriced": [], "listed": []}
    try:
        calm = q1("SELECT id, price_cents FROM products WHERE slug=%s", ("crittr-calm",))
        if calm and int(calm["price_cents"]) != CALM_PRICE_CENTS:
            q("UPDATE products SET price_cents=%s, compare_price_cents=%s WHERE id=%s",
              (CALM_PRICE_CENTS, CALM_COMPARE_CENTS, calm["id"]), fetch=False)
            out["repriced"].append(
                f"CRITTR Calm {calm['price_cents']} -> {CALM_PRICE_CENTS} (postage included)")

        if not q1("SELECT 1 AS x FROM products WHERE slug=%s", (JOINT["slug"],)):
            cat = q1("SELECT id FROM categories WHERE slug=%s", ("joint-mobility",))
            q("""INSERT INTO products
                 (category_id, name, slug, description, price_cents, compare_price_cents,
                  species, requires_rx, in_stock, tags, dosage_info, warnings)
                 VALUES (%s,%s,%s,%s,%s,%s,%s,FALSE,FALSE,%s,%s,%s)""",
              ((cat or {}).get("id"), JOINT["name"], JOINT["slug"], JOINT["description"],
               JOINT["price_cents"], JOINT["compare_cents"], JOINT["species"],
               JOINT["tags"], JOINT["dosage"], JOINT["warnings"]), fetch=False)
            out["listed"].append(f"{JOINT['name']} (in_stock=FALSE — no supplier yet)")

        q("""INSERT INTO crittr_meta(key, value) VALUES (%s,%s)
             ON CONFLICT (key) DO NOTHING""", (_MARKER, "applied"), fetch=False)
    except Exception as e:                                  # noqa: BLE001
        log.warning("[one_price] failed: %s", e)
        return {"applied": False, "reason": str(e)[:150]}
    log.info("[one_price] %s", out)
    return out
