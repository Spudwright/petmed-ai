"""crittr.ai — subscription waitlist (Phase D.7).

Pre-fulfillment capture: pet owners who want auto-ship at 15% off can
drop their email into a waitlist.  When crittr lands a 3PL or drop-ship
partner (Chewy Connexity, ShipBob, or direct private-label), we email
the list and convert to real subscribers.

This is a signaling and list-building layer — no billing, no Stripe
subscriptions, no Stripe plans yet.  Zero operational risk.

Public API
----------
    ensure_subscription_schema(q)
    register_subscription_routes(app, q)
"""
from __future__ import annotations

import logging
import re

from flask import jsonify, request

log = logging.getLogger("crittr.subscriptions")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def ensure_subscription_schema(q) -> None:
    try:
        q(
            """CREATE TABLE IF NOT EXISTS subscription_waitlist (
                 id BIGSERIAL PRIMARY KEY,
                 email TEXT NOT NULL,
                 product_slug TEXT,
                 product_name TEXT,
                 pet_species TEXT,
                 referrer TEXT,
                 source TEXT,
                 created_at TIMESTAMPTZ DEFAULT NOW()
               )""",
            fetch=False,
        )
        q(
            "CREATE UNIQUE INDEX IF NOT EXISTS subscription_waitlist_email_slug_idx "
            "ON subscription_waitlist (lower(email), coalesce(product_slug, ''))",
            fetch=False,
        )
    except Exception as e:
        log.warning("ensure_subscription_schema: %s", e)


def register_subscription_routes(app, q) -> None:
    ensure_subscription_schema(q)

    @app.route("/api/subscribe-waitlist", methods=["POST"])
    def api_waitlist():
        d = request.json or {}
        email = (d.get("email") or "").strip().lower()
        if not _EMAIL_RE.match(email):
            return jsonify({"error": "Please enter a valid email"}), 400
        product_slug = (d.get("product_slug") or "").strip()[:120] or None
        product_name = (d.get("product_name") or "").strip()[:200] or None
        pet_species  = (d.get("pet_species")  or "").strip()[:40]  or None
        referrer     = (request.referrer or "")[:400] or None
        source       = (d.get("source") or "otc-card")[:40]

        try:
            q(
                "INSERT INTO subscription_waitlist "
                "(email, product_slug, product_name, pet_species, referrer, source) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (lower(email), coalesce(product_slug, '')) DO NOTHING",
                (email, product_slug, product_name, pet_species, referrer, source),
                fetch=False,
            )
        except Exception as e:
            log.warning("waitlist insert failed: %s", e)
            return jsonify({"error": "Could not save — please try again."}), 500

        return jsonify({
            "ok": True,
            "message": (
                "You're on the list. We'll email you the moment auto-ship launches — "
                "first 500 signups get 20% off their first order (up from the standard 15%)."
            ),
        })
