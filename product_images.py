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
    # All 16 products now use AI-generated product photos committed to
    # /static/products/<slug>.png  (Phase E.7 completion)
    "frontline-gold":           "/static/products/frontline-gold.png",
    "seresto-collar":           "/static/products/seresto-collar.png",
    "crittr-combo-rx-chew":     "/static/products/crittr-combo-rx-chew.png",
    "crittr-heartworm-chew":    "/static/products/crittr-heartworm-chew.png",
    "crittr-cat-broad-topical": "/static/products/crittr-cat-broad-topical.png",
    "crittr-rx-gastro-diet":    "/static/products/crittr-rx-gastro-diet.png",
    "nexgard-plus":             "/static/products/crittr-combo-rx-chew.png",
    "heartgard-plus":           "/static/products/crittr-heartworm-chew.png",
    "revolution-plus":          "/static/products/crittr-cat-broad-topical.png",
    "purina-en":                "/static/products/crittr-rx-gastro-diet.png",
    "cosequin-ds-msm":          "/static/products/cosequin-ds-msm.png",
    "dasuquin-advanced":        "/static/products/dasuquin-advanced.png",
    "adaptil-calm":             "/static/products/adaptil-calm.png",
    "composure-pro":            "/static/products/composure-pro.png",
    "fortiflora":               "/static/products/fortiflora.png",
    "welactin-omega3":          "/static/products/welactin-omega3.png",
    "greenies-original":        "/static/products/greenies-original.png",
    "oravet-chews":             "/static/products/oravet-chews.png",
    "pet-tabs-plus":            "/static/products/pet-tabs-plus.png",
    "nucat-multivitamin":       "/static/products/nucat-multivitamin.png",
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
