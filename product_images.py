"""crittr.ai — idempotent seed for products.image_url.

Maps each of the current catalog SKUs to a stylised SVG tile in
/static/product-tiles/.  Runs once on startup; if a product already has a
non-null image_url, we leave it alone so hand-set images take priority.

Public API
----------
    ensure_product_images(q)
"""
from __future__ import annotations

import logging

log = logging.getLogger("crittr.product_images")


# slug -> tile filename (under /static/product-tiles/)
_TILE_MAP = {
    # flea-tick
    "frontline-gold":      "topical.svg",
    "nexgard-plus":        "chew.svg",
    "seresto-collar":      "collar.svg",
    # heartworm
    "heartgard-plus":      "chew.svg",
    "revolution-plus":     "topical.svg",
    # joint-mobility
    "cosequin-ds-msm":     "jar.svg",
    "dasuquin-advanced":   "jar.svg",
    # anxiety-calming
    "adaptil-calm":        "diffuser.svg",
    "composure-pro":       "chew.svg",
    # digestive
    "fortiflora":          "sachet.svg",
    "purina-en":           "food-bag.svg",
    # skin-coat
    "welactin-omega3":     "bottle.svg",
    # dental
    "greenies-original":   "chew.svg",
    "oravet-chews":        "chew.svg",
    # vitamins
    "pet-tabs-plus":       "jar.svg",
    "nucat-multivitamin":  "jar.svg",
}

# Category-level fallback: if a product isn't in _TILE_MAP we guess by
# category_slug + requires_rx so new SKUs automatically get *something*.
_CAT_FALLBACK = {
    "flea-tick":       "topical.svg",
    "heartworm":       "chew.svg",
    "joint-mobility":  "jar.svg",
    "anxiety-calming": "chew.svg",
    "digestive":       "sachet.svg",
    "skin-coat":       "bottle.svg",
    "dental":          "chew.svg",
    "vitamins":        "jar.svg",
}


def _tile_for(row) -> str | None:
    """Return the tile filename for a DB row, or None if we can't guess."""
    slug = row.get("slug") if hasattr(row, "get") else row["slug"]
    if slug in _TILE_MAP:
        return _TILE_MAP[slug]
    cat = row.get("category_slug") if hasattr(row, "get") else row["category_slug"]
    return _CAT_FALLBACK.get(cat)


def ensure_product_images(q) -> None:
    """Set products.image_url for every row where it is NULL or empty.

    Idempotent: rows with an existing non-empty image_url are never touched,
    so human-set images always win.
    """
    try:
        rows = q(
            "SELECT p.id, p.slug, p.image_url, c.slug AS category_slug "
            "FROM products p LEFT JOIN categories c ON p.category_id = c.id"
        ) or []
    except Exception as e:
        log.warning("ensure_product_images: could not read products: %s", e)
        return

    updated = 0
    skipped_present = 0
    skipped_unmapped = 0
    for r in rows:
        existing = r.get("image_url") if hasattr(r, "get") else r["image_url"]
        if existing:
            skipped_present += 1
            continue
        tile = _tile_for(r)
        if not tile:
            skipped_unmapped += 1
            continue
        url = f"/static/product-tiles/{tile}"
        try:
            q("UPDATE products SET image_url=%s WHERE id=%s", (url, r["id"]))
            updated += 1
        except Exception as e:
            log.warning("ensure_product_images: update failed for %s: %s",
                        r.get("slug"), e)

    if updated:
        log.info(
            "ensure_product_images: seeded %d tiles (%d already set, %d unmapped)",
            updated, skipped_present, skipped_unmapped,
        )
