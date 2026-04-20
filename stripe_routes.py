"""
Stripe integration for crittr.ai
--------------------------------
Drop-in module that adds:
  - POST /api/checkout          (one-time order -> Stripe Checkout)
  - POST /api/subscribe         (auto-refill subscription -> Stripe Checkout)
  - POST /api/portal            (Stripe Customer Portal session)
  - POST /api/stripe/webhook    (Stripe event receiver, signature-verified)
  - GET  /api/subscriptions     (user's active subscriptions)

Wire-up from app.py (two lines, after `app = Flask(...)` and helpers are defined):

    from stripe_routes import register_stripe_routes
    register_stripe_routes(app, q=q, q1=q1, login_required=login_required, get_db=get_db)

Required env vars (set on Railway):
    STRIPE_SECRET_KEY          sk_test_... or sk_live_...
    STRIPE_PUBLISHABLE_KEY     pk_test_... or pk_live_...
    STRIPE_WEBHOOK_SECRET      whsec_... (from Stripe dashboard -> Webhooks)
    APP_URL                    e.g. https://crittr.ai or the Railway URL
    STRIPE_MEMBERSHIP_PRICE_ID (optional) pre-created monthly membership price

Monetary amounts throughout crittr are integers in cents. This module preserves that.
"""

import os
import json
import stripe
from flask import request, jsonify, session


_q = None
_q1 = None
_get_db = None
_login_required = None


def ensure_stripe_schema():
    """Add Stripe-related columns and tables. Idempotent."""
    _q("ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT;", fetch=False)
    _q("ALTER TABLE products ADD COLUMN IF NOT EXISTS stripe_product_id TEXT;", fetch=False)
    _q("ALTER TABLE products ADD COLUMN IF NOT EXISTS stripe_price_monthly_id TEXT;", fetch=False)
    _q("ALTER TABLE products ADD COLUMN IF NOT EXISTS stripe_price_quarterly_id TEXT;", fetch=False)
    _q("ALTER TABLE orders ADD COLUMN IF NOT EXISTS stripe_session_id TEXT;", fetch=False)
    _q(
        """
        CREATE TABLE IF NOT EXISTS subscriptions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
            stripe_subscription_id TEXT UNIQUE NOT NULL,
            stripe_customer_id TEXT NOT NULL,
            stripe_price_id TEXT NOT NULL,
            interval TEXT NOT NULL,
            status TEXT NOT NULL,
            current_period_end TIMESTAMP,
            cancel_at_period_end BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );
        """,
        fetch=False,
    )
    _q("CREATE INDEX IF NOT EXISTS idx_subs_user ON subscriptions(user_id);", fetch=False)


def _get_or_create_stripe_customer(user_row):
    if user_row.get("stripe_customer_id"):
        return user_row["stripe_customer_id"]
    customer = stripe.Customer.create(
        email=user_row["email"],
        name=user_row.get("name") or None,
        metadata={"crittr_user_id": str(user_row["id"])},
    )
    _q(
        "UPDATE users SET stripe_customer_id = %s WHERE id = %s;",
        (customer.id, user_row["id"]),
        fetch=False,
    )
    return customer.id


def _ensure_product_recurring_price(product_row, interval):
    interval_col = {
        "monthly": "stripe_price_monthly_id",
        "quarterly": "stripe_price_quarterly_id",
    }[interval]
    interval_count = {"monthly": 1, "quarterly": 3}[interval]
    if product_row.get(interval_col):
        return product_row[interval_col]
    product_id = product_row.get("stripe_product_id")
    if not product_id:
        product = stripe.Product.create(
            name=product_row["name"],
            description=(product_row.get("description") or "")[:500] or None,
            metadata={"crittr_product_id": str(product_row["id"])},
        )
        product_id = product.id
        _q(
            "UPDATE products SET stripe_product_id = %s WHERE id = %s;",
            (product_id, product_row["id"]),
            fetch=False,
        )
    price = stripe.Price.create(
        product=product_id,
        unit_amount=int(product_row["price_cents"]),
        currency="usd",
        recurring={"interval": "month", "interval_count": interval_count},
    )
    _q(
        f"UPDATE products SET {interval_col} = %s WHERE id = %s;",
        (price.id, product_row["id"]),
        fetch=False,
    )
    return price.id


def register_stripe_routes(app, q, q1, login_required, get_db):
    global _q, _q1, _get_db, _login_required
    _q = q
    _q1 = q1
    _get_db = get_db
    _login_required = login_required

    stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    app_url = os.environ.get(
        "APP_URL", "https://web-production-39c1b.up.railway.app"
    ).rstrip("/")
    membership_price_id = os.environ.get("STRIPE_MEMBERSHIP_PRICE_ID", "")

    try:
        ensure_stripe_schema()
    except Exception as e:
        app.logger.warning(f"[stripe_routes] migration skipped/failed: {e}")

    @app.route("/api/checkout", methods=["POST"])
    @login_required
    def api_checkout():
        if not stripe.api_key:
            return jsonify({"error": "Stripe is not configured"}), 503
        data = request.json or {}
        items = data.get("items") or []
        shipping_address = data.get("shipping_address") or {}
        if not items:
            return jsonify({"error": "Cart is empty"}), 400
        user = q1("SELECT * FROM users WHERE id = %s;", (session["user_id"],))
        if not user:
            return jsonify({"error": "User not found"}), 404

        line_items = []
        order_items = []
        subtotal = 0
        for it in items:
            pid = int(it.get("product_id"))
            qty = max(1, int(it.get("quantity", 1)))
            prod = q1("SELECT * FROM products WHERE id = %s;", (pid,))
            if not prod:
                return jsonify({"error": f"Product {pid} not found"}), 404
            if not prod.get("in_stock"):
                return jsonify({"error": f"Product {prod['name']} is out of stock"}), 409
            line_items.append({
                "price_data": {
                    "currency": "usd",
                    "unit_amount": int(prod["price_cents"]),
                    "product_data": {
                        "name": prod["name"],
                        "description": (prod.get("description") or "")[:500] or None,
                    },
                },
                "quantity": qty,
            })
            order_items.append({
                "product_id": prod["id"],
                "name": prod["name"],
                "price_cents": int(prod["price_cents"]),
                "quantity": qty,
            })
            subtotal += int(prod["price_cents"]) * qty

        tax = int(round(subtotal * 0.08))
        shipping = 0 if subtotal >= 5000 else 599
        total = subtotal + tax + shipping

        if tax:
            line_items.append({
                "price_data": {
                    "currency": "usd",
                    "unit_amount": tax,
                    "product_data": {"name": "Sales tax"},
                },
                "quantity": 1,
            })
        if shipping:
            line_items.append({
                "price_data": {
                    "currency": "usd",
                    "unit_amount": shipping,
                    "product_data": {"name": "Shipping"},
                },
                "quantity": 1,
            })

        customer_id = _get_or_create_stripe_customer(user)

        pending = q1(
            """
            INSERT INTO orders (user_id, status, items, subtotal_cents, tax_cents,
                                shipping_cents, total_cents, shipping_address)
            VALUES (%s, 'pending', %s::jsonb, %s, %s, %s, %s, %s::jsonb)
            RETURNING id;
            """,
            (
                user["id"],
                json.dumps(order_items),
                subtotal, tax, shipping, total,
                json.dumps(shipping_address),
            ),
        )
        order_id = pending["id"]

        checkout = stripe.checkout.Session.create(
            mode="payment",
            customer=customer_id,
            line_items=line_items,
            success_url=f"{app_url}/order/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{app_url}/cart?canceled=1",
            metadata={
                "crittr_order_id": str(order_id),
                "crittr_user_id": str(user["id"]),
                "flow": "one_time_order",
            },
            payment_intent_data={
                "metadata": {"crittr_order_id": str(order_id)},
            },
        )

        q(
            "UPDATE orders SET stripe_session_id = %s WHERE id = %s;",
            (checkout.id, order_id),
            fetch=False,
        )

        return jsonify({"url": checkout.url, "session_id": checkout.id, "order_id": order_id})

    @app.route("/api/subscribe", methods=["POST"])
    @login_required
    def api_subscribe():
        if not stripe.api_key:
            return jsonify({"error": "Stripe is not configured"}), 503
        data = request.json or {}
        user = q1("SELECT * FROM users WHERE id = %s;", (session["user_id"],))
        if not user:
            return jsonify({"error": "User not found"}), 404

        customer_id = _get_or_create_stripe_customer(user)
        plan = data.get("plan")

        if plan == "membership":
            if not membership_price_id:
                return jsonify({
                    "error": "Membership price not configured. Set STRIPE_MEMBERSHIP_PRICE_ID."
                }), 503
            line_items = [{"price": membership_price_id, "quantity": 1}]
            metadata = {"flow": "membership", "crittr_user_id": str(user["id"])}
        else:
            pid = int(data.get("product_id", 0))
            interval = data.get("interval", "monthly")
            if interval not in ("monthly", "quarterly"):
                return jsonify({"error": "interval must be 'monthly' or 'quarterly'"}), 400
            prod = q1("SELECT * FROM products WHERE id = %s;", (pid,))
            if not prod:
                return jsonify({"error": "Product not found"}), 404
            price_id = _ensure_product_recurring_price(prod, interval)
            line_items = [{"price": price_id, "quantity": 1}]
            metadata = {
                "flow": "auto_refill",
                "crittr_user_id": str(user["id"]),
                "crittr_product_id": str(prod["id"]),
                "interval": interval,
            }

        checkout = stripe.checkout.Session.create(
            mode="subscription",
            customer=customer_id,
            line_items=line_items,
            success_url=f"{app_url}/account/subscriptions?success=1",
            cancel_url=f"{app_url}/account/subscriptions?canceled=1",
            metadata=metadata,
            subscription_data={"metadata": metadata},
        )
        return jsonify({"url": checkout.url, "session_id": checkout.id})

    @app.route("/api/portal", methods=["POST"])
    @login_required
    def api_portal():
        user = q1("SELECT * FROM users WHERE id = %s;", (session["user_id"],))
        if not user or not user.get("stripe_customer_id"):
            return jsonify({"error": "No Stripe customer on file"}), 404
        portal = stripe.billing_portal.Session.create(
            customer=user["stripe_customer_id"],
            return_url=f"{app_url}/account",
        )
        return jsonify({"url": portal.url})

    @app.route("/api/subscriptions", methods=["GET"])
    @login_required
    def api_list_subscriptions():
        rows = q(
            """
            SELECT s.*, p.name AS product_name, p.image_url
            FROM subscriptions s
            LEFT JOIN products p ON p.id = s.product_id
            WHERE s.user_id = %s
            ORDER BY s.created_at DESC;
            """,
            (session["user_id"],),
        )
        return jsonify({"subscriptions": rows or []})

    @app.route("/api/stripe/webhook", methods=["POST"])
    def api_stripe_webhook():
        if not webhook_secret:
            return jsonify({"error": "Webhook secret not configured"}), 503
        payload = request.data
        sig = request.headers.get("Stripe-Signature", "")
        try:
            event = stripe.Webhook.construct_event(payload, sig, webhook_secret)
        except stripe.error.SignatureVerificationError:
            return jsonify({"error": "Invalid signature"}), 400
        except Exception as e:
            return jsonify({"error": f"Bad payload: {e}"}), 400

        etype = event["type"]
        obj = event["data"]["object"]

        try:
            if etype == "checkout.session.completed":
                md = obj.get("metadata") or {}
                flow = md.get("flow")
                if flow == "one_time_order":
                    order_id = int(md.get("crittr_order_id", 0))
                    payment_intent = obj.get("payment_intent")
                    if order_id:
                        q(
                            """
                            UPDATE orders
                            SET status = 'paid', stripe_payment_id = %s
                            WHERE id = %s;
                            """,
                            (payment_intent, order_id),
                            fetch=False,
                        )

            elif etype in ("customer.subscription.created",
                           "customer.subscription.updated"):
                md = obj.get("metadata") or {}
                user_id = int(md.get("crittr_user_id", 0)) or None
                product_id = int(md.get("crittr_product_id", 0)) or None
                interval = md.get("interval") or md.get("flow") or "membership"
                sub_id = obj["id"]
                customer_id = obj["customer"]
                status = obj["status"]
                current_period_end = obj.get("current_period_end")
                cancel_at_period_end = bool(obj.get("cancel_at_period_end"))
                price_id = None
                try:
                    price_id = obj["items"]["data"][0]["price"]["id"]
                except Exception:
                    pass

                if not user_id:
                    u = _q1(
                        "SELECT id FROM users WHERE stripe_customer_id = %s;",
                        (customer_id,),
                    )
                    user_id = u["id"] if u else None

                if user_id:
                    _q(
                        """
                        INSERT INTO subscriptions
                            (user_id, product_id, stripe_subscription_id, stripe_customer_id,
                             stripe_price_id, interval, status, current_period_end,
                             cancel_at_period_end, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, to_timestamp(%s), %s, NOW())
                        ON CONFLICT (stripe_subscription_id) DO UPDATE SET
                            status = EXCLUDED.status,
                            current_period_end = EXCLUDED.current_period_end,
                            cancel_at_period_end = EXCLUDED.cancel_at_period_end,
                            stripe_price_id = EXCLUDED.stripe_price_id,
                            updated_at = NOW();
                        """,
                        (user_id, product_id, sub_id, customer_id, price_id,
                         interval, status, current_period_end, cancel_at_period_end),
                        fetch=False,
                    )

            elif etype == "customer.subscription.deleted":
                _q(
                    """
                    UPDATE subscriptions
                    SET status = 'canceled', updated_at = NOW()
                    WHERE stripe_subscription_id = %s;
                    """,
                    (obj["id"],),
                    fetch=False,
                )

            elif etype == "invoice.payment_failed":
                sub_id = obj.get("subscription")
                if sub_id:
                    _q(
                        """
                        UPDATE subscriptions
                        SET status = 'past_due', updated_at = NOW()
                        WHERE stripe_subscription_id = %s;
                        """,
                        (sub_id,),
                        fetch=False,
                    )

        except Exception as e:
            app.logger.error(f"[stripe_webhook] handler error on {etype}: {e}")
            return jsonify({"error": "handler failed"}), 500

        return jsonify({"received": True}), 200
