"""crittr.ai — idempotent seed for products.image_url.

Phase E.6: upgraded from static SVG tiles to Unsplash lifestyle photos.
Each of 16 SKUs gets a thoughtfully-mapped real photo.  Re-seeds when
image_url is NULL OR is an old /static/product-tiles/<x>.svg path, so
deploys upgrade placeholder tiles but never clobber manually-set photos.

Public API
----------
    ensure_product_images(q)
"""
from __future__ import annotations

import logging

log = logging.getLogger("crittr.product_images")


# slug -> image URL
#   3 slugs have AI-generated product photos (Phase E.7) under /static/products/
#   the remaining 13 use Unsplash lifestyle photos until we generate more
_IMAGE_URL_MAP = {
    # Flea-tick / parasite — AI-generated product shots
    "frontline-gold":           "/static/products/frontline-gold.png",
    "seresto-collar":           "/static/products/seresto-collar.png",
    # Rebranded Rx combo chews + topical
    "crittr-combo-rx-chew":     "https://images.unsplash.com/photo-1601758228041-f3b2795255f1?w=900&q=80",
    "crittr-heartworm-chew":    "https://images.unsplash.com/photo-1543466835-00a7907e9de1?w=900&q=80",
    "crittr-cat-broad-topical": "https://images.unsplash.com/photo-1592194996308-7b43878e84a6?w=900&q=80",
    "crittr-rx-gastro-diet":    "https://images.unsplash.com/photo-1583337130417-3346a1be7dee?w=900&q=80",
    # Legacy slugs (in case rebrand didn't run)
    "nexgard-plus":             "https://images.unsplash.com/photo-1601758228041-f3b2795255f1?w=900&q=80",
    "heartgard-plus":           "https://images.unsplash.com/photo-1543466835-00a7907e9de1?w=900&q=80",
    "revolution-plus":          "https://images.unsplash.com/photo-1592194996308-7b43878e84a6?w=900&q=80",
    "purina-en":                "https://images.unsplash.com/photo-1583337130417-3346a1be7dee?w=900&q=80",
    # Joint / mobility
    "cosequin-ds-msm":          "/static/products/cosequin-ds-msm.png",
    "dasuquin-advanced":        "https://images.unsplash.com/photo-1507146426996-ef05306b995a?w=900&q=80",
    # Calming / behavior
    "adaptil-calm":             "https://images.unsplash.com/photo-1535930891776-0c2dfb7fda1a?w=900&q=80",
    "composure-pro":            "https://images.unsplash.com/photo-1592194996308-7b43878e84a6?w=900&q=80",
    # Digestive / probiotic
    "fortiflora":               "https://images.unsplash.com/photo-1543466835-00a7907e9de1?w=900&q=80",
    # Skin / coat
    "welactin-omega3":          "https://images.unsplash.com/photo-1573865526739-10659fec78a5?w=900&q=80",
    # Dental
    "greenies-original":        "https://images.unsplash.com/photo-1587300003388-59208cc962cb?w=900&q=80",
    "oravet-chews":             "https://images.unsplash.com/photo-1583337130417-3346a1be7dee?w=900&q=80",
    # Multivitamins
    "pet-tabs-plus":            "https://images.unsplash.com/photo-1437957146754-f6377debe171?w=900&q=80",
    "nucat-multivitamin":       "https://images.unsplash.com/photo-1592194996308-7b43878e84a6?w=900&q=80",
}

# Fallback: category_slug -> photo URL, in case a new product is added
_CATEGORY_FALLBACK = {
    "flea-tick":       "https://images.unsplash.com/photo-1587300003388-59208cc962cb?w=900&q=80",
    "heartworm":       "https://images.unsplash.com/photo-1601758228041-f3b2795255f1?w=900&q=80",
    "joint-mobility":  "https://images.unsplash.com/photo-1507146426996-ef05306b995a?w=900&q=80",
    "anxiety-calming": "https://images.unsplash.com/photo-1535930891776-0c2dfb7fda1a?w=900&q=80",
    "digestive":       "https://images.unsplash.com/photo-1543466835-00a7907e9de1?w=900&q=80",
    "skin-coat":       "https://images.unsplash.com/photo-1573865526739-10659fec78a5?w=900&q=80",
    "dental":          "https://images.unsplash.com/photo-1587300003388-59208cc962cb?w=900&q=80",
    "vitamins":        "https://images.unsplash.com/photo-1437957146754-f6377debe171?w=900&q=80",
}


def _photo_for(row):
    slug = row.get("slug") if hasattr(row, "get") else row["slug"]
    if slug in _IMAGE_URL_MAP:
        return _IMAGE_URL_MAP[slug]
    cat = row.get("category_slug") if hasattr(row, "get") else row["category_slug"]
    return _CATEGORY_FALLBACK.get(cat)


def ensure_product_images(q) -> None:
    """Seed/upgrade products.image_url with lifestyle photos.

    Writes when image_url is NULL/empty OR still points to an old
    /static/product-tiles/ SVG path.  Hand-set URLs (anything else) are
    preserved.
    """
    try:
        rows = q(
            "SELECT p.id, p.slug, p.image_url, c.slug AS category_slug "
            "FROM products p LEFT JOIN categories c ON p.category_id = c.id"
        ) or []
    except Exception as e:
        log.warning("ensure_product_images: could not read products: %s", e)
        return

    updated = skipped_present = skipped_unmapped = 0
    for r in rows:
        existing = r.get("image_url") if hasattr(r, "get") else r["image_url"]
        # Overwrite if blank, old tile path, or the current map value is newer
        # (e.g. swapping Unsplash placeholder for a new /static/products/*.png).
        url = _photo_for(r)
        if not url:
            skipped_unmapped += 1
            continue
        is_blank = not existing
        is_tile = bool(existing) and "/static/product-tiles/" in existing
        # Upgrade Unsplash -> local product photo automatically
        is_stale_unsplash = (bool(existing) and "images.unsplash.com" in existing
                             and url.startswith("/static/products/"))
        if not (is_blank or is_tile or is_stale_unsplash):
            skipped_present += 1
            continue
        try:
            q("UPDATE products SET image_url=%s WHERE id=%s", (url, r["id"]), fetch=False)
            updated += 1
        except Exception as e:
            log.warning("ensure_product_images: update failed for %s: %s",
                        r.get("slug"), e)

    if updated:
        log.info(
            "ensure_product_images: seeded %d photos (%d preserved, %d unmapped)",
            updated, skipped_present, skipped_unmapped,
        )
