"""crittr.ai — Phase B.7 Rx rebrand.

Converts the 4 Rx products from retail brand names (Heartgard Plus, NexGard
Plus, Revolution Plus, Purina Pro Plan EN) to crittr's own generic/compounded
equivalents.

Runs idempotently on startup. Uses a `rebrand_marker` row in the
`crittr_meta` table (auto-created) so the rename executes once per slug.
Old slugs become 301 redirects via register_rx_rebrand_redirects(app).

Public API
----------
    ensure_rx_rebrand(q)
    register_rx_rebrand_redirects(app)
"""
from __future__ import annotations

import logging
from flask import redirect

log = logging.getLogger("crittr.rx_rebrand")


# old_slug -> new spec
_REBRAND = {
    "nexgard-plus": {
        "new_slug":       "crittr-combo-rx-chew",
        "new_name":       "crittr flea, tick & heartworm chew",
        "new_desc":      "Monthly soft chew that covers fleas, ticks and heartworm in one go. Compounded for crittr; available after a quick vet consult.",
        "new_image":      "/static/product-tiles/crittr-flea-tick-heartworm-chew.svg",
    },
    "heartgard-plus": {
        "new_slug":       "crittr-heartworm-chew",
        "new_name":       "crittr monthly heartworm chew",
        "new_desc":      "Monthly heartworm protection in a beef-flavored chew. Compounded for crittr; available after a quick vet consult.",
        "new_image":      "/static/product-tiles/crittr-heartworm-chew.svg",
    },
    "revolution-plus": {
        "new_slug":       "crittr-cat-broad-topical",
        "new_name":       "crittr broad-spectrum cat topical",
        "new_desc":      "Monthly topical for cats — fleas, ticks, heartworm, ear mites and worms. Compounded for crittr; available after a quick vet consult.",
        "new_image":      "/static/product-tiles/crittr-cat-broad-topical.svg",
    },
    "purina-en": {
        "new_slug":       "crittr-rx-gastro-diet",
        "new_name":       "crittr Rx gastro diet",
        "new_desc":      "Vet-prescribed therapeutic food for dogs with GI upset. Our private-label formulation, shipped after a quick vet consult.",
        "new_image":      "/static/product-tiles/crittr-rx-gastro.svg",
    },
}


def _ensure_meta_table(q):
    try:
        q("""CREATE TABLE IF NOT EXISTS crittr_meta (
              key TEXT PRIMARY KEY,
              value TEXT,
              updated_at TIMESTAMPTZ DEFAULT NOW())""", fetch=False)
    except Exception as e:
        log.warning("ensure_rx_rebrand: could not ensure crittr_meta: %s", e)


def ensure_rx_rebrand(q) -> None:
    """Rename the 4 Rx SKUs to their crittr-branded equivalents.

    Idempotent: each slug is only renamed once; subsequent startups see the
    marker and skip.  Never touches OTC products.
    """
    _ensure_meta_table(q)
    for old_slug, spec in _REBRAND.items():
        marker_key = f"rx_rebrand_v1:{old_slug}"
        try:
            already = q(
                "SELECT value FROM crittr_meta WHERE key=%s",
                (marker_key,),
            )
        except Exception:
            already = None
        if already:
            continue

        # Does the old SKU still exist?
        try:
            row = q(
                "SELECT id, name, requires_rx FROM products WHERE slug=%s",
                (old_slug,),
            )
        except Exception as e:
            log.warning("ensure_rx_rebrand: read failed for %s: %s", old_slug, e)
            continue
        if not row:
            # nothing to rename — still set the marker so we don't keep looking
            try:
                q(
                    "INSERT INTO crittr_meta(key, value) VALUES (%s, %s) "
                    "ON CONFLICT (key) DO NOTHING",
                    (marker_key, "skipped:not-found"),
                    fetch=False,
                )
            except Exception:
                pass
            continue

        r = row[0] if isinstance(row, list) else row
        if not (r.get("requires_rx") if hasattr(r, "get") else r["requires_rx"]):
            log.warning(
                "ensure_rx_rebrand: refusing to rename OTC product %s", old_slug
            )
            continue

        try:
            q(
                "UPDATE products SET slug=%s, name=%s, description=%s, image_url=%s "
                "WHERE slug=%s",
                (
                    spec["new_slug"],
                    spec["new_name"],
                    spec["new_desc"],
                    spec["new_image"],
                    old_slug,
                ),
                fetch=False,
            )
            q(
                "INSERT INTO crittr_meta(key, value) VALUES (%s, %s) "
                "ON CONFLICT (key) DO NOTHING",
                (marker_key, spec["new_slug"]),
                fetch=False,
            )
            log.info(
                "ensure_rx_rebrand: %s -> %s", old_slug, spec["new_slug"]
            )
        except Exception as e:
            log.warning("ensure_rx_rebrand: update failed for %s: %s", old_slug, e)


def register_rx_rebrand_redirects(app) -> None:
    """Register 301 redirects so old product URLs keep working."""
    for old_slug, spec in _REBRAND.items():
        new_slug = spec["new_slug"]

        # /shop/<old_slug> isn't a real pattern we use (shop is category-based),
        # but /api/products/<old_slug> IS — point it at the new slug.
        endpoint = f"_rx_rebrand_redirect_{old_slug.replace('-', '_')}"
        route = f"/api/products/{old_slug}"

        def make_view(new=new_slug):
            def _view():
                return redirect(f"/api/products/{new}", code=301)
            _view.__name__ = endpoint
            return _view

        try:
            app.add_url_rule(route, endpoint, make_view(), methods=["GET"])
        except Exception as e:
            log.warning("register_rx_rebrand_redirects: %s failed: %s", route, e)


def register_rebrand_admin(app, q):
    """Admin-only manual trigger: GET /admin/rebrand-rx?key=<ADMIN_TOKEN>.

    Returns JSON with what actually happened, so we can diagnose silent
    startup failures.  Remove after rebrand is confirmed in prod.
    """
    import os
    from flask import request, jsonify

    @app.route("/admin/rebrand-rx")
    def _admin_rebrand_rx():
        token = os.environ.get("ADMIN_TOKEN", "")
        key = request.args.get("key", "")
        # If no token configured, accept a hardcoded dev key so the user can
        # trigger once then we'll remove this endpoint.
        expected = token or "crittr-rebrand-2026"
        if not key or key != expected:
            return jsonify({"error": "unauthorized"}), 403

        report = {"steps": []}

        # Step 1: create marker table
        try:
            q("""CREATE TABLE IF NOT EXISTS crittr_meta (
                   key TEXT PRIMARY KEY,
                   value TEXT,
                   updated_at TIMESTAMPTZ DEFAULT NOW())""", fetch=False)
            report["steps"].append("crittr_meta table ensured")
        except Exception as e:
            report["steps"].append(f"crittr_meta CREATE failed: {type(e).__name__}: {e}")

        # Step 2: list current Rx products
        try:
            rx_now = q("SELECT slug, name, requires_rx FROM products WHERE requires_rx=TRUE")
            report["rx_before"] = [dict(r) for r in (rx_now or [])]
        except Exception as e:
            report["rx_before_err"] = f"{type(e).__name__}: {e}"

        # Step 3: do the rebrand for each
        report["updates"] = []
        for old_slug, spec in _REBRAND.items():
            try:
                row = q("SELECT id, slug, requires_rx FROM products WHERE slug=%s", (old_slug,))
                if not row:
                    report["updates"].append({"slug": old_slug, "result": "not-found"})
                    continue
                r = row[0] if isinstance(row, list) else row
                if not r.get("requires_rx"):
                    report["updates"].append({"slug": old_slug, "result": "refused-otc"})
                    continue
                q(
                    "UPDATE products SET slug=%s, name=%s, description=%s, image_url=%s "
                    "WHERE slug=%s",
                    (spec["new_slug"], spec["new_name"], spec["new_desc"],
                     spec["new_image"], old_slug),
                    fetch=False,
                )
                report["updates"].append({"slug": old_slug, "result": "renamed",
                                          "new_slug": spec["new_slug"]})
            except Exception as e:
                report["updates"].append({"slug": old_slug,
                                          "result": f"err: {type(e).__name__}: {e}"})

        # Step 4: re-read Rx products
        try:
            rx_after = q("SELECT slug, name FROM products WHERE requires_rx=TRUE")
            report["rx_after"] = [dict(r) for r in (rx_after or [])]
        except Exception as e:
            report["rx_after_err"] = f"{type(e).__name__}: {e}"

        return jsonify(report)
