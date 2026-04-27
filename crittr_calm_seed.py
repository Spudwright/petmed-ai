"""crittr.ai — Phase H.9: seed CRITTR Calm OTC supplement.

First CRITTR-branded white-label OTC SKU. Inserts an Anxiety & Calming
soft-chew product called "CRITTR Calm" priced at $29.99 (compare $34.99).
Idempotent via crittr_meta marker.

Public API
----------
    ensure_crittr_calm(q)
"""
from __future__ import annotations

import logging

log = logging.getLogger("crittr.calm_seed")


_MARKER_KEY = "crittr_calm_v1:seeded"

_SLUG = "crittr-calm"
_NAME = "CRITTR Calm"
_PRICE_CENTS = 2999          # $29.99
_COMPARE_CENTS = 3499        # $34.99
_SPECIES = "dog"
_REQUIRES_RX = False
_DESCRIPTION = (
    "Calming soft chews for dogs. Natural anxiety and stress support with "
    "L-theanine, chamomile, hemp, and valerian root. Chicken flavor. "
    "Veterinarian-formulated for fireworks, thunderstorms, separation, "
    "travel, and vet visits. 60 soft chews per jar."
)
_DOSAGE = (
    "Up to 25 lbs: 1 chew. 25-75 lbs: 2 chews. Over 75 lbs: 3 chews. "
    "Give 30-60 minutes before stressful events or daily for ongoing support."
)
_WARNINGS = (
    "Consult your veterinarian before use if your dog is pregnant, nursing, "
    "or on other medications. Keep out of reach of children."
)
_IMAGE_URL = "/static/products/crittr-calm.png"
_TAGS = "supplement,calming,anxiety,otc,crittr-brand"


def _ensure_meta_table(q):
    try:
        q(
            """CREATE TABLE IF NOT EXISTS crittr_meta (
              key TEXT PRIMARY KEY,
              value TEXT,
              updated_at TIMESTAMPTZ DEFAULT NOW())""",
            fetch=False,
        )
    except Exception as e:
        log.warning("crittr_calm_seed: could not ensure crittr_meta: %s", e)


def _resolve_calming_category_id(q):
    """Return the id of the 'anxiety-calming' category, or None."""
    try:
        rows = q(
            "SELECT id FROM categories WHERE slug=%s",
            ("anxiety-calming",),
        )
    except Exception as e:
        log.warning("crittr_calm_seed: category lookup failed: %s", e)
        return None
    if not rows:
        return None
    r = rows[0] if isinstance(rows, list) else rows
    return r.get("id") if hasattr(r, "get") else r["id"]


def _existing_product(q):
    try:
        rows = q("SELECT id FROM products WHERE slug=%s", (_SLUG,))
    except Exception:
        return None
    if not rows:
        return None
    r = rows[0] if isinstance(rows, list) else rows
    return r.get("id") if hasattr(r, "get") else r["id"]


def _column_exists(q, table, column):
    try:
        rows = q(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name=%s AND column_name=%s",
            (table, column),
        )
    except Exception:
        return False
    return bool(rows)


def ensure_crittr_calm(q) -> None:
    """Insert CRITTR Calm if not already present.

    Idempotent. If the product already exists, ensures image_url is set
    to the canonical CRITTR-branded jar.
    """
    _ensure_meta_table(q)

    try:
        marker = q("SELECT value FROM crittr_meta WHERE key=%s", (_MARKER_KEY,))
    except Exception:
        marker = None

    pid = _existing_product(q)
    if pid:
        try:
            q(
                "UPDATE products SET image_url=%s WHERE id=%s AND "
                "(image_url IS NULL OR image_url='' OR image_url <> %s)",
                (_IMAGE_URL, pid, _IMAGE_URL),
                fetch=False,
            )
        except Exception as e:
            log.warning("crittr_calm_seed: image update failed: %s", e)
        if not marker:
            try:
                q(
                    "INSERT INTO crittr_meta(key, value) VALUES (%s, %s) "
                    "ON CONFLICT (key) DO NOTHING",
                    (_MARKER_KEY, "exists"),
                    fetch=False,
                )
            except Exception:
                pass
        return

    cat_id = _resolve_calming_category_id(q)
    if not cat_id:
        log.warning("crittr_calm_seed: anxiety-calming category not found; skipping")
        return

    has_tags = _column_exists(q, "products", "tags")
    has_image = _column_exists(q, "products", "image_url")

    cols = [
        "name", "slug", "category_id", "price_cents", "compare_price_cents",
        "species", "requires_rx", "description", "dosage_info", "warnings",
    ]
    vals = [
        _NAME, _SLUG, cat_id, _PRICE_CENTS, _COMPARE_CENTS,
        _SPECIES, _REQUIRES_RX, _DESCRIPTION, _DOSAGE, _WARNINGS,
    ]
    if has_image:
        cols.append("image_url")
        vals.append(_IMAGE_URL)
    if has_tags:
        cols.append("tags")
        vals.append(_TAGS)

    placeholders = ",".join(["%s"] * len(cols))
    sql = f"INSERT INTO products ({','.join(cols)}) VALUES ({placeholders})"

    try:
        q(sql, tuple(vals), fetch=False)
        q(
            "INSERT INTO crittr_meta(key, value) VALUES (%s, %s) "
            "ON CONFLICT (key) DO NOTHING",
            (_MARKER_KEY, "inserted"),
            fetch=False,
        )
        log.info("crittr_calm_seed: inserted CRITTR Calm (slug=%s)", _SLUG)
    except Exception as e:
        log.warning("crittr_calm_seed: insert failed: %s", e)
