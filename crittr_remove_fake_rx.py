"""crittr.ai — Phase H.10: remove the 4 fake CRITTR Rx generics.

The 4 Rx products created by Phase B.7 (crittr-combo-rx-chew,
crittr-heartworm-chew, crittr-cat-broad-topical, crittr-rx-gastro-diet)
have no real pharmacy fulfillment, no prescription workflow, and no
inventory. This module deletes them so the catalog only shows real
products: 12 OTC Amazon affiliates + CRITTR Calm.

Idempotent via crittr_meta marker. Falls back to hiding (requires_rx=true,
tags='hidden') if FK constraints block delete.

Public API
----------
    ensure_fake_rx_removed(q)
"""
from __future__ import annotations

import logging

log = logging.getLogger("crittr.remove_fake_rx")


_MARKER_KEY = "remove_fake_rx_v1:done"

_FAKE_RX_SLUGS = (
    "crittr-combo-rx-chew",
    "crittr-heartworm-chew",
    "crittr-cat-broad-topical",
    "crittr-rx-gastro-diet",
)


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
        log.warning("remove_fake_rx: could not ensure crittr_meta: %s", e)


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


def ensure_fake_rx_removed(q) -> None:
    """Delete the 4 fake Rx products. Idempotent."""
    _ensure_meta_table(q)
    try:
        already = q("SELECT value FROM crittr_meta WHERE key=%s", (_MARKER_KEY,))
    except Exception:
        already = None
    if already:
        return

    has_tags = _column_exists(q, "products", "tags")
    removed = hidden = 0
    for slug in _FAKE_RX_SLUGS:
        try:
            rows = q("SELECT id FROM products WHERE slug=%s", (slug,))
        except Exception as e:
            log.warning("remove_fake_rx: lookup failed for %s: %s", slug, e)
            continue
        if not rows:
            continue
        r = rows[0] if isinstance(rows, list) else rows
        pid = r.get("id") if hasattr(r, "get") else r["id"]

        # Try DELETE first; if FK constraints block, fall back to hide
        try:
            q("DELETE FROM products WHERE id=%s", (pid,), fetch=False)
            removed += 1
            log.info("remove_fake_rx: deleted %s", slug)
        except Exception as delete_err:
            try:
                if has_tags:
                    q(
                        "UPDATE products SET tags='hidden,inactive' WHERE id=%s",
                        (pid,),
                        fetch=False,
                    )
                else:
                    # without tags col, set price to 0 and slug to hidden
                    q(
                        "UPDATE products SET price_cents=0, name=%s WHERE id=%s",
                        (f"[hidden] {slug}", pid),
                        fetch=False,
                    )
                hidden += 1
                log.info("remove_fake_rx: hid %s (delete blocked: %s)",
                         slug, delete_err)
            except Exception as hide_err:
                log.warning("remove_fake_rx: hide also failed for %s: %s",
                            slug, hide_err)

    try:
        q(
            "INSERT INTO crittr_meta(key, value) VALUES (%s, %s) "
            "ON CONFLICT (key) DO NOTHING",
            (_MARKER_KEY, f"removed={removed},hidden={hidden}"),
            fetch=False,
        )
    except Exception:
        pass

    if removed or hidden:
        log.info("remove_fake_rx: removed=%d hidden=%d", removed, hidden)
