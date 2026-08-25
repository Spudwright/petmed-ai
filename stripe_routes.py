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
import logging
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
    _q("ALTER TABLE orders ADD COLUMN IF NOT EXISTS credit_applied_cents INT DEFAULT 0;", fetch=False)
    _q("ALTER TABLE orders ADD COLUMN IF NOT EXISTS recovery_email_sent_at TIMESTAMPTZ;", fetch=False)
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
    def api_checkout():
        if not stripe.api_key:
            return jsonify({"error": "Stripe is not configured"}), 503
        data = request.json or {}
        items = data.get("items") or []
        shipping_address = data.get("shipping_address") or {}
        if not items:
            return jsonify({"error": "Cart is empty"}), 400

        # Guest-checkout friendly: user is optional. If logged in, we apply
        # referral credits + stamp the order with user_id. If not, Stripe
        # Checkout collects email on their page and we receive it via webhook.
        user_id = session.get("user_id")
        user = None
        if user_id:
            user = q1("SELECT * FROM users WHERE id = %s;", (user_id,))
        is_guest = user is None

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
                    # Stripe Tax needs to know whether the price already contains tax.
                    # 'exclusive' = tax is added at checkout, which is the US convention
                    # and what a customer expects. Shipping is baked into the price;
                    # sales tax deliberately is not, because absorbing a rate that varies
                    # from 0% to over 10% by destination would silently eat the margin in
                    # exactly the states you sell most in.
                    "tax_behavior": "exclusive",
                    "product_data": {
                        "name": prod["name"],
                        "description": (prod.get("description") or "")[:500] or None,
                        # Dietary/nutritional supplement. Several states tax supplements
                        # differently from general goods (and a few not at all), which is
                        # the entire reason a hardcoded rate was wrong.
                        "tax_code": os.environ.get("STRIPE_TAX_CODE_SUPPLEMENT",
                                                   "txcd_40060003"),
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

        # MEMBER DISCOUNT. crittr Care promises 10% off the shop; this is where that
        # promise has to become money, or the membership is a page of adjectives. Applied
        # to the subtotal before tax, so the member sees it on the Stripe page.
        member_discount = 0
        if user:
            try:
                from vet_aftercare import is_member
                if is_member(q1, user["id"])[0]:
                    member_discount = int(round(subtotal * 0.10))
            except Exception as _me:
                app.logger.warning(f"[checkout] member lookup failed: {_me}")

        # ONE PRICE. Shipping is baked into the ticket price — no shipping line, no
        # free-over-$50 threshold, nothing to work out at checkout. Postage costs roughly
        # $7 on a 1lb jar and varies by about $3 between the nearest and furthest zone;
        # averaging that into the price is worth more in conversion than the variance
        # costs, and it is what the supplement category does.
        shipping = 0
        # TAX IS NOW STRIPE'S JOB, NOT A HARDCODED 8%. The old line charged every customer
        # 8% regardless of destination — over-collecting in low-rate states, under-
        # collecting in high-rate ones, and charging tax at all in states where crittr has
        # no nexus and no registration. Stripe Tax computes it per destination and tracks
        # registration thresholds. The real figure comes back on the webhook; this is a
        # placeholder for the pending row only.
        tax = 0
        total = subtotal

        customer_id = None if is_guest else _get_or_create_stripe_customer(user)

        # ---- Referral credit redemption (Phase 8) — logged-in users only ----
        credit_applied = 0
        coupon_id = None
        credit_balance = 0
        if not is_guest:
            try:
                from referrals import get_credit_balance
                credit_balance = get_credit_balance(q, user["id"])
            except Exception as _ce:
                app.logger.warning(f"[checkout] credit balance lookup failed: {_ce}")
                credit_balance = 0
        if credit_balance > 0 and subtotal > 0:
            credit_applied = min(credit_balance, subtotal)
            try:
                coupon = stripe.Coupon.create(
                    amount_off=credit_applied,
                    currency="usd",
                    duration="once",
                    max_redemptions=1,
                    name=f"crittr credit (${credit_applied/100:.2f})",
                    metadata={"crittr_user_id": str(user["id"])},
                )
                coupon_id = coupon.id
                # Recompute totals so the stored order reflects the discount.
                total = max(0, subtotal - credit_applied) + tax + shipping
            except Exception as _cpe:
                app.logger.warning(f"[checkout] Stripe coupon create failed: {_cpe}")
                coupon_id = None
                credit_applied = 0

        pending = q1(
            """
            INSERT INTO orders (user_id, status, items, subtotal_cents, tax_cents,
                                shipping_cents, total_cents, shipping_address,
                                credit_applied_cents)
            VALUES (%s, 'pending', %s::jsonb, %s, %s, %s, %s, %s::jsonb, %s)
            RETURNING id;
            """,
            (
                user["id"] if user else None,
                json.dumps(order_items),
                subtotal, tax, shipping, total,
                json.dumps(shipping_address),
                credit_applied,
            ),
        )
        order_id = pending["id"]

        # Debit the ledger now (idempotent on order_id via reason tag).
        if credit_applied > 0 and user:
            try:
                from referrals import record_credit_debit
                record_credit_debit(q, user["id"], credit_applied, f"checkout_pending:{order_id}")
            except Exception as _de:
                app.logger.warning(f"[checkout] ledger debit failed: {_de}")

        session_kwargs = dict(
            mode="payment",
            line_items=line_items,
            success_url=f"{app_url}/order/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{app_url}/cart?canceled=1",
            metadata={
                "crittr_order_id": str(order_id),
                "crittr_user_id": str(user["id"]) if user else "guest",
                "flow": "one_time_order",
                "credit_applied_cents": str(credit_applied),
            },
            payment_intent_data={
                "metadata": {"crittr_order_id": str(order_id)},
            },
            # Stripe Tax. It cannot compute a destination rate without a destination, so
            # the shipping address is collected for it — that address is also what the
            # supplier's purchase order needs, so nothing extra is asked of the customer.
            automatic_tax={"enabled": True},
            shipping_address_collection={"allowed_countries": ["US"]},
        )
        if customer_id:
            session_kwargs["customer"] = customer_id
            # Required by Stripe when automatic_tax is on and a customer is attached:
            # the address Stripe taxes against has to be allowed to update from checkout.
            session_kwargs["customer_update"] = {"address": "auto", "shipping": "auto"}
        else:
            # Guest flow — let Stripe collect email + create a customer record
            session_kwargs["customer_creation"] = "always"
            # Optional: if cart drawer sent guest_email, prefill it
            guest_email = (data.get("guest_email") or "").strip()
            if guest_email:
                session_kwargs["customer_email"] = guest_email[:200]
        if coupon_id:
            session_kwargs["discounts"] = [{"coupon": coupon_id}]

        checkout = stripe.checkout.Session.create(**session_kwargs)

        q(
            "UPDATE orders SET stripe_session_id = %s WHERE id = %s;",
            (checkout.id, order_id),
            fetch=False,
        )

        return jsonify({
            "url": checkout.url,
            "session_id": checkout.id,
            "order_id": order_id,
            "credit_applied_cents": credit_applied,
        })

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
                if flow == "consult":
                    # A paid telehealth consult. The money has ALREADY split — it settled
                    # into the vet's connected account with crittr's fee retained at the
                    # moment of payment — so this only records what Stripe already did.
                    # Note this must live inside the checkout.session.completed branch:
                    # an elif on etype further down would never be reached.
                    try:
                        from consults import mark_paid
                        _cid = int(md.get("crittr_consult_id", 0))
                        if _cid:
                            mark_paid(q, q1, _cid, obj.get("payment_intent"))
                    except Exception as _ce:
                        logging.getLogger(__name__).error(
                            f"[consults] consult paid but not recorded: {_ce}")
                elif flow == "one_time_order":
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
                        # THE REAL TAX COMES BACK FROM STRIPE, NOT FROM US. The pending row
                        # was written with tax 0 because the destination was not known yet
                        # — Stripe Tax computes it at checkout against the shipping address.
                        # Write the authoritative figures back, or the order record and the
                        # confirmation email both under-report what the customer paid.
                        try:
                            _td = obj.get("total_details") or {}
                            q(
                                """UPDATE orders SET tax_cents = %s, total_cents = %s
                                   WHERE id = %s""",
                                (int(_td.get("amount_tax") or 0),
                                 int(obj.get("amount_total") or 0), order_id),
                                fetch=False,
                            )
                        except Exception as _te:
                            logging.getLogger(__name__).error(
                                f"[tax] order {order_id} kept its placeholder totals: {_te}")
                        # REVENUE SHARE. Credit the vet or the practice for this order.
                        # Wrapped because a failure here must never look like a failed
                        # payment to a customer who has already been charged — but it logs
                        # at error level, because a silent zero would hide a broken partner
                        # payout for a month and clinics leave over exactly that.
                        try:
                            from vet_practice import attribute_order
                            _o = q1(
                                "SELECT user_id, items FROM orders WHERE id = %s",
                                (order_id,),
                            )
                            if _o and _o.get("user_id"):
                                attribute_order(
                                    q, q1,
                                    order_id=order_id,
                                    items=_o.get("items"),
                                    owner_user_id=_o.get("user_id"),
                                )
                        except Exception as _ae:
                            import logging
                            logging.getLogger(__name__).error(
                                f"[attribution] order {order_id} not credited: {_ae}")

                        # FULFILMENT. Split the paid order into one purchase order per
                        # supplier and send them. Wrapped for the same reason as
                        # attribution: the customer has already paid, so a supplier-side
                        # problem must not surface as a failed payment. It logs at error
                        # level because an order that never becomes a purchase order is
                        # money taken for a box that will not arrive.
                        try:
                            from dropship import create_fulfilments
                            _fr = create_fulfilments(q, q1, order_id)
                            if _fr.get("unfulfillable"):
                                logging.getLogger(__name__).error(
                                    f"[dropship] order {order_id} has lines with NO "
                                    f"supplier: {_fr['unfulfillable']} — will not ship")
                        except Exception as _fe:
                            logging.getLogger(__name__).error(
                                f"[dropship] order {order_id} not routed to a supplier: "
                                f"{_fe}")

                        try:
                            from emails import send_order_confirmation
                            cust = obj.get("customer_details") or {}
                            to_email = cust.get("email") or obj.get("customer_email")
                            ship_det = obj.get("shipping_details") or {}
                            ship_addr = ship_det.get("address") if ship_det else None
                            cust_name = (ship_det.get("name") if ship_det else None) or cust.get("name")
                            td = obj.get("total_details") or {}
                            items = []
                            db_subtotal = None
                            db_tax = None
                            db_shipping = None
                            db_total = None
                            try:
                                row = q(
                                    "SELECT items, subtotal_cents, tax_cents, shipping_cents, total_cents FROM orders WHERE id = %s",
                                    (order_id,), fetch="one",
                                )
                                if row:
                                    if isinstance(row, dict):
                                        raw_items = row.get("items")
                                        db_subtotal = row.get("subtotal_cents")
                                        db_tax = row.get("tax_cents")
                                        db_shipping = row.get("shipping_cents")
                                        db_total = row.get("total_cents")
                                    else:
                                        raw_items = row[0] if hasattr(row, "__getitem__") else None
                                        try:
                                            db_subtotal = row[1]; db_tax = row[2]; db_shipping = row[3]; db_total = row[4]
                                        except Exception:
                                            pass
                                    if raw_items:
                                        if isinstance(raw_items, str):
                                            import json as _json
                                            try:
                                                items = _json.loads(raw_items)
                                            except Exception:
                                                items = []
                                        elif isinstance(raw_items, list):
                                            items = raw_items
                            except Exception:
                                pass
                            if to_email:
                                send_order_confirmation(
                                    to_email=to_email, name=cust_name, order_id=order_id, items=items,
                                    subtotal_cents=db_subtotal if db_subtotal is not None else (obj.get("amount_subtotal") or 0),
                                    tax_cents=db_tax if db_tax is not None else (td.get("amount_tax") or 0),
                                    shipping_cents=db_shipping if db_shipping is not None else (td.get("amount_shipping") or 0),
                                    total_cents=db_total if db_total is not None else (obj.get("amount_total") or 0),
                                    shipping_address=ship_addr,
                                )
                        except Exception as _e:
                            import logging
                            logging.getLogger(__name__).error(f"[emails] webhook send failed: {_e}")

            elif etype in ("charge.refunded", "charge.dispute.closed",
                           "charge.dispute.created"):
                # THE MONEY WENT BACK, SO THE CREDIT MUST TOO. Without this a clinic keeps
                # its share of a sale the customer reversed, paid out of crittr's margin,
                # and nobody notices until a statement is queried months later. A partial
                # refund claws back proportionally; a dispute we LOST is a full reversal,
                # while one we won leaves the credit alone.
                reverse = True
                reason = etype
                amount = None
                if etype == "charge.dispute.closed":
                    reverse = (obj.get("status") == "lost")
                    reason = f"dispute_{obj.get('status')}"
                    amount = None
                elif etype == "charge.dispute.created":
                    reverse = False          # not yet decided — wait for closed
                else:
                    amount = obj.get("amount_refunded")
                if reverse:
                    try:
                        from vet_practice import reverse_order
                        pi = obj.get("payment_intent") or (
                            obj.get("charge") if isinstance(obj.get("charge"), str) else None)
                        _row = q1(
                            "SELECT id FROM orders WHERE stripe_payment_id = %s", (pi,)
                        ) if pi else None
                        if _row:
                            res = reverse_order(q, q1, order_id=_row["id"],
                                                refunded_cents=amount, reason=reason)
                            logging.getLogger(__name__).info(
                                f"[attribution] {reason} on order {_row['id']}: {res}")
                        else:
                            logging.getLogger(__name__).warning(
                                f"[attribution] {reason}: no order matches payment_intent "
                                f"{pi} — a vet may retain credit for reversed money")
                    except Exception as _re:
                        logging.getLogger(__name__).error(
                            f"[attribution] reversal failed on {etype}: {_re}")

            elif etype in ("checkout.session.expired", "checkout.session.async_payment_failed"):
                md = obj.get("metadata") or {}
                flow = md.get("flow")
                if flow == "one_time_order":
                    try:
                        order_id = int(md.get("crittr_order_id", 0))
                        uid = int(md.get("crittr_user_id", 0))
                        applied = int(md.get("credit_applied_cents", 0))
                    except Exception:
                        order_id = uid = applied = 0
                    if order_id and uid and applied > 0:
                        try:
                            from referrals import record_credit_reversal
                            record_credit_reversal(q, uid, applied, f"checkout_reversed:{order_id}")
                        except Exception as _re:
                            import logging
                            logging.getLogger(__name__).warning(f"[webhook] credit reversal failed: {_re}")
                    if order_id:
                        try:
                            q("UPDATE orders SET status = 'expired' WHERE id = %s AND status = 'pending';", (order_id,), fetch=False)
                        except Exception:
                            pass
                    if order_id and uid:
                        try:
                            row = _q1(
                                "SELECT o.items, o.subtotal_cents, u.email, u.name "
                                "FROM orders o JOIN users u ON u.id = o.user_id "
                                "WHERE o.id = %s AND o.user_id = %s "
                                "AND (o.recovery_email_sent_at IS NULL);",
                                (order_id, uid),
                            )
                            if row:
                                _ritems = row.get("items")
                                if isinstance(_ritems, str):
                                    import json as _json
                                    try:
                                        _ritems = _json.loads(_ritems)
                                    except Exception:
                                        _ritems = []
                                _sub = int(row.get("subtotal_cents") or 0)
                                _email = row.get("email")
                                _name = row.get("name")
                                _balance = 0
                                try:
                                    from referrals import get_credit_balance
                                    _balance = int(get_credit_balance(q, uid) or 0)
                                except Exception:
                                    pass
                                app_url = os.environ.get("APP_URL", "https://crittr.ai").rstrip("/")
                                _resume_url = f"{app_url}/#/cart"
                                if _email:
                                    from emails import send_abandoned_cart_email
                                    send_abandoned_cart_email(
                                        to_email=_email, name=_name, items=_ritems or [],
                                        subtotal_cents=_sub, credit_balance_cents=_balance,
                                        checkout_url=_resume_url,
                                    )
                                    q("UPDATE orders SET recovery_email_sent_at = NOW() WHERE id = %s;", (order_id,), fetch=False)
                        except Exception as _ae:
                            import logging
                            logging.getLogger(__name__).warning(f"[webhook] abandoned cart send failed: {_ae}")

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
                    # THE MISSING WIRE. Until now this recorded the subscription and
                    # stopped — nothing ever wrote to care_members, so is_member() was
                    # false for everyone and a paying member got nothing for their money.
                    # crittr Care has existed as a priced tier with a working gate and no
                    # way to actually become a member.
                    if md.get("flow") == "membership":
                        try:
                            from vet_aftercare import start_membership
                            if status in ("active", "trialing"):
                                start_membership(_q, _q1, user_id, tier="care",
                                                 stripe_sub_id=sub_id)
                                logging.getLogger(__name__).info(
                                    f"[care] user {user_id} is now a member ({status})")
                            else:
                                _q("""UPDATE care_members SET status=%s
                                      WHERE user_id=%s""", (status, user_id), fetch=False)
                        except Exception as _me:
                            logging.getLogger(__name__).error(
                                f"[care] user {user_id} PAID but membership not "
                                f"activated: {_me}")

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
                # And take the membership away. A cancelled subscriber who keeps unlimited
                # triage forever is a slow leak that nobody notices for months.
                try:
                    _q("""UPDATE care_members SET status='canceled'
                          WHERE stripe_sub_id = %s""", (obj["id"],), fetch=False)
                except Exception as _ce:
                    logging.getLogger(__name__).error(
                        f"[care] subscription {obj['id']} cancelled but membership "
                        f"still active: {_ce}")

            elif etype == "invoice.payment_succeeded":
                # THE RENEWAL HOOK. Nothing handled this event before, so a membership
                # activated once at checkout and then renewed in silence — which was
                # survivable while the membership paid nobody but crittr, and is not now
                # that a practice earns a share of every month the client stays.
                #
                # Membership invoices are identified through care_members rather than
                # invoice metadata: Stripe only copies checkout metadata onto the
                # subscription when subscription_data.metadata was set, and care_members
                # is where a membership demonstrably is. A product auto-refill invoice
                # has no row here and is correctly ignored.
                sub_id = obj.get("subscription")
                paid = int(obj.get("amount_paid") or 0)
                inv_id = obj.get("id")
                if sub_id and paid > 0:
                    try:
                        _m = _q1("""SELECT user_id FROM care_members
                                    WHERE stripe_sub_id=%s""", (sub_id,))
                        if _m:
                            from member_plan import credit_subscription
                            credit_subscription(_q, _q1, user_id=_m["user_id"],
                                                invoice_id=inv_id, amount_cents=paid)
                    except Exception as _se:
                        # Loud, and not fatal: the member has paid and must keep their
                        # access. An uncredited invoice is a reconciliation job; a 500
                        # here would make Stripe retry a payment that already succeeded.
                        logging.getLogger(__name__).error(
                            f"[member_plan] invoice {inv_id} paid but practice not "
                            f"credited: {_se}")

            elif etype == "charge.refunded":
                # A refunded membership month has to come back off the practice's
                # statement, or crittr pays out money it gave back to the owner.
                _inv = obj.get("invoice")
                if _inv:
                    try:
                        from member_plan import reverse_subscription
                        reverse_subscription(_q, _q1, invoice_id=_inv, reason="refund")
                    except Exception as _re:
                        logging.getLogger(__name__).error(
                            f"[member_plan] invoice {_inv} refunded but credit not "
                            f"reversed: {_re}")

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
