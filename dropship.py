"""crittr.ai — getting the box to the door, without owning a warehouse.

THE PROBLEM THIS SOLVES, WHICH IS EMBARRASSING AND CURRENT. An order today goes from
'pending' to 'paid' and then nothing happens to it. There is no supplier, no purchase
order, no tracking, no state after payment. CRITTR Calm is in_stock with an "Add to cart"
button, so a customer can pay $29.99 for something that has no route to their house. That
is the same condition that got four fake Rx products deleted in Phase H.10, and it is worth
naming plainly rather than describing this file as a feature.

WHY IT IS BUILT SUPPLIER-AGNOSTIC. crittr has not signed a supplier. Building against a
specific partner's API before choosing the partner is how you end up rebuilding it. So the
supplier is a row, the route is a strategy, and adding a real integration later means
writing one adapter rather than touching the order flow.

WHY 'EMAIL' IS THE DEFAULT ROUTE AND NOT A PLACEHOLDER. At the volumes crittr is at, a
purchase order emailed to a supplier IS the integration. It works with any supplier on the
day you agree terms, needs nothing from their engineering team, and costs nothing. An API
adapter is worth building when the volume makes the manual step expensive — not before.
Sequencing it the other way round has killed more small e-commerce than bad products have.

THE INVARIANT WORTH THE MOST. A product cannot be sold unless it has a route to the
customer. `fulfillable()` is the single source of that truth, and `enforce_stock_matches_
fulfilment()` makes the shop obey it — so "we took their money and nothing shipped" becomes
structurally impossible rather than something to remember.
"""
import os
import json
import logging
from datetime import datetime, timezone

from flask import request, jsonify, session

log = logging.getLogger("crittr.dropship")

# How a purchase order reaches the supplier.
ROUTE_EMAIL = "email"      # a PO lands in their inbox — works with anyone, day one
ROUTE_MANUAL = "manual"    # crittr places it by hand; the record still exists
ROUTE_API = "api"          # reserved for a real integration, once volume earns it

F_PENDING = "pending"          # paid, not yet sent to the supplier
F_SUBMITTED = "submitted"      # PO sent
F_SHIPPED = "shipped"          # tracking exists
F_DELIVERED = "delivered"
F_FAILED = "failed"            # supplier could not fill it — someone must refund
F_CANCELLED = "cancelled"


def init_dropship_tables(q):
    q("""
    CREATE TABLE IF NOT EXISTS suppliers (
        id              SERIAL PRIMARY KEY,
        name            TEXT NOT NULL,
        contact_email   TEXT,
        -- 'email' | 'manual' | 'api'. Email is the default on purpose; see the module
        -- docstring. An API adapter is a volume decision, not a maturity one.
        route           TEXT NOT NULL DEFAULT 'email',
        lead_time_days  INTEGER DEFAULT 3,
        notes           TEXT,
        status          TEXT NOT NULL DEFAULT 'active',
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""", fetch=False)
    q("""
    CREATE TABLE IF NOT EXISTS product_suppliers (
        product_id      INTEGER PRIMARY KEY REFERENCES products(id) ON DELETE CASCADE,
        supplier_id     INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
        supplier_sku    TEXT,
        -- Cost belongs to the SUPPLIER relationship, not the product: the same thing from
        -- two suppliers costs two amounts. It is mirrored onto products.cost_cents so the
        -- margin screen keeps working off one column.
        cost_cents      INTEGER,
        updated_at      TIMESTAMPTZ DEFAULT NOW()
    )""", fetch=False)
    q("""
    CREATE TABLE IF NOT EXISTS fulfilments (
        id              SERIAL PRIMARY KEY,
        order_id        INTEGER NOT NULL,
        supplier_id     INTEGER REFERENCES suppliers(id) ON DELETE SET NULL,
        -- One fulfilment per supplier per order: a basket spanning two suppliers becomes
        -- two shipments, which is what actually happens in the world.
        items           JSONB NOT NULL DEFAULT '[]',
        status          TEXT NOT NULL DEFAULT 'pending',
        supplier_ref    TEXT,
        tracking_carrier TEXT,
        tracking_number TEXT,
        failure         TEXT,
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        submitted_at    TIMESTAMPTZ,
        shipped_at      TIMESTAMPTZ,
        delivered_at    TIMESTAMPTZ,
        UNIQUE (order_id, supplier_id)
    )""", fetch=False)
    q("""CREATE INDEX IF NOT EXISTS idx_fulfilments_open ON fulfilments(status)
         WHERE status IN ('pending','submitted')""", fetch=False)


# ── can this product actually reach a customer? ──────────────────────────────

def fulfillable(q1, product_id):
    """The single source of truth for 'may we sell this'. Returns (bool, reason)."""
    row = q1("""SELECT s.id, s.name, s.status, s.route
                FROM product_suppliers ps JOIN suppliers s ON s.id = ps.supplier_id
                WHERE ps.product_id = %s""", (product_id,))
    if not row:
        return False, "no supplier linked — nothing would ship"
    if row["status"] != "active":
        return False, f"supplier '{row['name']}' is {row['status']}"
    return True, ""


def enforce_stock_matches_fulfilment(q):
    """Take anything off sale that has no route to a customer. Idempotent.

    Affiliate products are exempt: their button sends the customer to Amazon, so crittr
    never owes anyone a box. Everything else must have a supplier or it comes down.
    """
    rows = q("""UPDATE products p SET in_stock = FALSE
                WHERE p.in_stock
                  AND COALESCE(p.amazon_url,'') = ''
                  AND NOT EXISTS (
                        SELECT 1 FROM product_suppliers ps
                        JOIN suppliers s ON s.id = ps.supplier_id AND s.status='active'
                        WHERE ps.product_id = p.id)
                RETURNING id, name""") or []
    if rows:
        log.warning("[dropship] took %s product(s) off sale with no fulfilment route: %s",
                    len(rows), ", ".join(r["name"] for r in rows))
    return [dict(r) for r in rows]


def link_supplier(q, q1, product_id, supplier_id, *, supplier_sku=None, cost_cents=None):
    q("""INSERT INTO product_suppliers (product_id, supplier_id, supplier_sku, cost_cents)
         VALUES (%s,%s,%s,%s)
         ON CONFLICT (product_id) DO UPDATE SET supplier_id=EXCLUDED.supplier_id,
             supplier_sku=EXCLUDED.supplier_sku, cost_cents=EXCLUDED.cost_cents,
             updated_at=NOW()""",
      (product_id, supplier_id, supplier_sku, cost_cents), fetch=False)
    if cost_cents is not None:
        # Mirror onto the product so /admin/products margin maths keeps working.
        q("UPDATE products SET cost_cents=%s WHERE id=%s", (int(cost_cents), product_id),
          fetch=False)
    return q1("SELECT * FROM product_suppliers WHERE product_id=%s", (product_id,))


# ── the flow: paid order -> purchase orders ──────────────────────────────────

def create_fulfilments(q, q1, order_id):
    """Split a paid order into one fulfilment per supplier. Idempotent per (order, supplier).

    Called from the Stripe webhook. Deliberately does not raise: an order the customer has
    already paid for must not fail because a supplier row is missing. It records the gap
    loudly instead, which is what the unfulfillable list on the admin screen is for.
    """
    existing = q("SELECT 1 FROM fulfilments WHERE order_id=%s LIMIT 1", (order_id,))
    if existing:
        return {"order_id": order_id, "skipped": "already has fulfilments"}
    o = q1("SELECT id, items, user_id, shipping_address FROM orders WHERE id=%s",
           (order_id,))
    if not o:
        return {"order_id": order_id, "error": "no such order"}
    items = o.get("items")
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except Exception:
            items = []
    by_supplier, orphans = {}, []
    for it in (items or []):
        pid = it.get("product_id")
        if not pid:
            continue
        row = q1("""SELECT ps.supplier_id, ps.supplier_sku FROM product_suppliers ps
                    JOIN suppliers s ON s.id=ps.supplier_id AND s.status='active'
                    WHERE ps.product_id=%s""", (pid,))
        if not row:
            orphans.append(it.get("name") or pid)
            continue
        line = dict(it)
        line["supplier_sku"] = row.get("supplier_sku")
        by_supplier.setdefault(row["supplier_id"], []).append(line)

    created = []
    for supplier_id, lines in by_supplier.items():
        f = q1("""INSERT INTO fulfilments (order_id, supplier_id, items, status)
                  VALUES (%s,%s,%s::jsonb,'pending')
                  ON CONFLICT (order_id, supplier_id) DO NOTHING
                  RETURNING id""", (order_id, supplier_id, json.dumps(lines)))
        if f:
            created.append(f["id"])
    if orphans:
        # This is the "we took their money and nothing ships" case, made visible.
        log.error("[dropship] order %s has %s line(s) with NO supplier: %s — these will "
                  "not ship and need manual handling or a refund",
                  order_id, len(orphans), ", ".join(str(x) for x in orphans))
    for fid in created:
        try:
            submit_fulfilment(q, q1, fid)
        except Exception as e:                              # noqa: BLE001
            log.error("[dropship] could not submit fulfilment %s: %s", fid, e)
    return {"order_id": order_id, "fulfilments": created, "unfulfillable": orphans}


def _po_lines(items):
    out = []
    for it in (items or []):
        sku = it.get("supplier_sku") or f"crittr-{it.get('product_id')}"
        out.append(f"  {it.get('quantity', 1)} x {sku} — {it.get('name', '')}")
    return "\n".join(out)


def submit_fulfilment(q, q1, fulfilment_id):
    """Send the purchase order. Email route = the PO lands in the supplier's inbox."""
    f = q1("""SELECT f.*, s.name AS supplier_name, s.contact_email, s.route
              FROM fulfilments f LEFT JOIN suppliers s ON s.id=f.supplier_id
              WHERE f.id=%s""", (fulfilment_id,))
    if not f:
        return None, "no such fulfilment"
    if f["status"] != F_PENDING:
        return f, "already submitted"
    o = q1("SELECT shipping_address FROM orders WHERE id=%s", (f["order_id"],))
    addr = (o or {}).get("shipping_address") or {}
    if isinstance(addr, str):
        try:
            addr = json.loads(addr)
        except Exception:
            addr = {}
    items = f["items"]
    if isinstance(items, str):
        items = json.loads(items)

    if f["route"] == ROUTE_EMAIL and f.get("contact_email"):
        ok = _email_po(f, items, addr)
        if not ok:
            q("UPDATE fulfilments SET failure=%s WHERE id=%s",
              ("purchase order email failed", fulfilment_id), fetch=False)
            return f, "the purchase order email could not be sent"
    # 'manual' records the intent without sending anything — someone places it by hand.
    q("""UPDATE fulfilments SET status='submitted', submitted_at=NOW(), failure=NULL
         WHERE id=%s""", (fulfilment_id,), fetch=False)
    log.info("[dropship] fulfilment %s submitted to %s via %s",
             fulfilment_id, f.get("supplier_name"), f.get("route"))
    return q1("SELECT * FROM fulfilments WHERE id=%s", (fulfilment_id,)), ""


def _email_po(f, items, addr):
    key = os.environ.get("RESEND_API_KEY", "")
    to = f.get("contact_email")
    if not (key and to):
        log.warning("[dropship] no RESEND_API_KEY or supplier email — PO for fulfilment "
                    "%s not sent", f["id"])
        return False
    ship = "\n".join(str(v) for v in [
        addr.get("name"), addr.get("line1"), addr.get("line2"),
        f"{addr.get('city','')} {addr.get('state','')} {addr.get('postal_code','')}".strip(),
        addr.get("country")] if v)
    body = (f"Purchase order — crittr #{f['order_id']}-{f['id']}\n\n"
            f"SHIP DIRECT TO:\n{ship or '(no address on file — please contact us)'}\n\n"
            f"ITEMS:\n{_po_lines(items)}\n\n"
            f"Please reply with a tracking number when despatched.\n"
            f"Questions: {os.environ.get('REPLY_TO_EMAIL', 'hello@crittr.ai')}\n")
    try:
        import resend
        resend.api_key = key
        resend.Emails.send({
            "from": os.environ.get("FROM_EMAIL", "crittr <hello@crittr.ai>"),
            "to": [to],
            "subject": f"crittr PO #{f['order_id']}-{f['id']} — ship direct to customer",
            "text": body,
            "reply_to": os.environ.get("REPLY_TO_EMAIL", "hello@crittr.ai"),
        })
        return True
    except Exception as e:                                  # noqa: BLE001
        log.error("[dropship] PO email failed for fulfilment %s: %s", f["id"], e)
        return False


def mark_shipped(q, q1, fulfilment_id, *, carrier=None, tracking=None, notify=True):
    """Record despatch and tell the customer. The one step a human always does."""
    f = q1("SELECT * FROM fulfilments WHERE id=%s", (fulfilment_id,))
    if not f:
        return None, "no such fulfilment"
    q("""UPDATE fulfilments SET status='shipped', shipped_at=NOW(),
         tracking_carrier=%s, tracking_number=%s WHERE id=%s""",
      (carrier, tracking, fulfilment_id), fetch=False)
    if notify:
        _notify_shipped(q1, f["order_id"], carrier, tracking)
    return q1("SELECT * FROM fulfilments WHERE id=%s", (fulfilment_id,)), ""


def _notify_shipped(q1, order_id, carrier, tracking):
    key = os.environ.get("RESEND_API_KEY", "")
    row = q1("""SELECT u.email, u.name FROM orders o JOIN users u ON u.id=o.user_id
                WHERE o.id=%s""", (order_id,))
    to = (row or {}).get("email")
    if not (key and to):
        log.warning("[dropship] order %s shipped but customer not notified "
                    "(no key or no email on the order)", order_id)
        return False
    t = f"\n\nTracking: {carrier or ''} {tracking or ''}".rstrip() if tracking else ""
    try:
        import resend
        resend.api_key = key
        resend.Emails.send({
            "from": os.environ.get("FROM_EMAIL", "crittr <hello@crittr.ai>"),
            "to": [to],
            "subject": f"Your crittr order #{order_id} is on its way",
            "text": f"Good news — your order has been despatched.{t}\n\n"
                    f"Any questions, just reply to this email.\n— crittr",
        })
        return True
    except Exception as e:                                  # noqa: BLE001
        log.error("[dropship] shipped notice failed for order %s: %s", order_id, e)
        return False


def mark_failed(q, q1, fulfilment_id, reason):
    """The supplier cannot fill it. Recorded so a refund is somebody's job, not a surprise."""
    q("UPDATE fulfilments SET status='failed', failure=%s WHERE id=%s",
      (str(reason)[:400], fulfilment_id), fetch=False)
    log.error("[dropship] fulfilment %s FAILED: %s — order needs a refund",
              fulfilment_id, reason)
    return q1("SELECT * FROM fulfilments WHERE id=%s", (fulfilment_id,))


def order_status(q, q1, order_id):
    """What the customer sees."""
    rows = q("""SELECT f.id, f.status, f.tracking_carrier, f.tracking_number,
                       f.shipped_at, s.name AS supplier, s.lead_time_days
                FROM fulfilments f LEFT JOIN suppliers s ON s.id=f.supplier_id
                WHERE f.order_id=%s ORDER BY f.id""", (order_id,)) or []
    st = [r["status"] for r in rows]
    overall = ("not_started" if not st else
               "delivered" if all(x == F_DELIVERED for x in st) else
               "shipped" if all(x in (F_SHIPPED, F_DELIVERED) for x in st) else
               "problem" if any(x == F_FAILED for x in st) else
               "partially_shipped" if any(x in (F_SHIPPED, F_DELIVERED) for x in st) else
               "preparing")
    return {"order_id": order_id, "status": overall,
            "shipments": [{k: (v if isinstance(v, (int, type(None))) else str(v))
                           for k, v in dict(r).items()} for r in rows]}


def open_work(q):
    """The despatch desk: everything paid and not yet shipped."""
    return [dict(r) for r in (q("""
        SELECT f.id, f.order_id, f.status, f.created_at, f.failure,
               s.name AS supplier, s.route, s.contact_email
        FROM fulfilments f LEFT JOIN suppliers s ON s.id=f.supplier_id
        WHERE f.status IN ('pending','submitted','failed')
        ORDER BY f.created_at""") or [])]


# ── HTTP ─────────────────────────────────────────────────────────────────────

def register_dropship_routes(app, q, q1, admin_required):

    @app.route("/api/admin/suppliers", methods=["GET", "POST"])
    @admin_required
    def api_suppliers():
        if request.method == "POST":
            d = request.get_json(silent=True) or {}
            name = (d.get("name") or "").strip()
            if not name:
                return jsonify({"error": "name is required"}), 400
            row = q1("""INSERT INTO suppliers (name, contact_email, route,
                                               lead_time_days, notes)
                        VALUES (%s,%s,%s,%s,%s) RETURNING *""",
                     (name, (d.get("contact_email") or "").strip() or None,
                      (d.get("route") or ROUTE_EMAIL), int(d.get("lead_time_days") or 3),
                      d.get("notes")))
            return jsonify({"ok": True, "supplier": _ser(row)})
        rows = q("SELECT * FROM suppliers ORDER BY name") or []
        return jsonify({"suppliers": [_ser(r) for r in rows]})

    @app.route("/api/admin/products/<int:product_id>/supplier", methods=["POST"])
    @admin_required
    def api_link_supplier(product_id):
        d = request.get_json(silent=True) or {}
        sid = d.get("supplier_id")
        if not sid:
            return jsonify({"error": "supplier_id is required"}), 400
        row = link_supplier(q, q1, product_id, int(sid),
                            supplier_sku=d.get("supplier_sku"),
                            cost_cents=d.get("cost_cents"))
        return jsonify({"ok": True, "link": _ser(row)})

    @app.route("/api/admin/fulfilments", methods=["GET"])
    @admin_required
    def api_open_fulfilments():
        return jsonify({"open": [_ser(r) for r in open_work(q)]})

    @app.route("/api/admin/fulfilments/<int:fid>/shipped", methods=["POST"])
    @admin_required
    def api_mark_shipped(fid):
        d = request.get_json(silent=True) or {}
        row, why = mark_shipped(q, q1, fid, carrier=d.get("carrier"),
                                tracking=d.get("tracking"))
        if not row:
            return jsonify({"error": why}), 400
        return jsonify({"ok": True, "fulfilment": _ser(row)})

    @app.route("/api/admin/fulfilments/<int:fid>/failed", methods=["POST"])
    @admin_required
    def api_mark_failed(fid):
        d = request.get_json(silent=True) or {}
        return jsonify({"ok": True, "fulfilment": _ser(
            mark_failed(q, q1, fid, d.get("reason") or "supplier could not fill"))})

    @app.route("/api/admin/enforce_stock", methods=["POST"])
    @admin_required
    def api_enforce_stock():
        pulled = enforce_stock_matches_fulfilment(q)
        return jsonify({"ok": True, "taken_off_sale": len(pulled),
                        "products": [p["name"] for p in pulled]})

    @app.route("/admin/fulfilments", methods=["GET"])
    @admin_required
    def admin_fulfilments_page():
        """The despatch desk. Everything paid and not yet in a box."""
        work = open_work(q)
        orphan = q("""SELECT p.id, p.name FROM products p
                      WHERE p.in_stock AND COALESCE(p.amazon_url,'')=''
                        AND NOT EXISTS (SELECT 1 FROM product_suppliers ps
                                        JOIN suppliers s ON s.id=ps.supplier_id
                                                        AND s.status='active'
                                        WHERE ps.product_id=p.id)""") or []
        rows = ""
        for w in work:
            bad = w["status"] == F_FAILED or w.get("failure")
            colour = "#A32020" if bad else "#3E6340"
            rows += (
                f"<tr><td style='padding:10px;border-top:1px solid #DFE5DB'>"
                f"#{w['order_id']}<div style='color:#6E7D70;font-size:12px'>"
                f"fulfilment {w['id']}</div></td>"
                f"<td style='padding:10px;border-top:1px solid #DFE5DB'>"
                f"{w.get('supplier') or '—'}<div style='color:#6E7D70;font-size:12px'>"
                f"{w.get('route') or ''} · {w.get('contact_email') or 'no email'}</div></td>"
                f"<td style='padding:10px;border-top:1px solid #DFE5DB;color:{colour}'>"
                f"<strong>{w['status']}</strong>"
                f"{'<div style=font-size:12px>' + str(w.get('failure'))[:70] + '</div>' if w.get('failure') else ''}</td>"
                f"<td style='padding:10px;border-top:1px solid #DFE5DB'>"
                f"<input placeholder='carrier' id='c{w['id']}' style='width:80px;padding:6px;"
                f"border:1px solid #DFE5DB;border-radius:6px'> "
                f"<input placeholder='tracking' id='t{w['id']}' style='width:150px;padding:6px;"
                f"border:1px solid #DFE5DB;border-radius:6px'> "
                f"<button onclick='ship({w['id']})' style='background:#527E54;color:#fff;"
                f"border:0;border-radius:7px;padding:7px 12px;font-weight:700;"
                f"cursor:pointer'>Shipped</button></td></tr>")
        if not rows:
            rows = ("<tr><td colspan=4 style='padding:28px;text-align:center;"
                    "color:#6E7D70'>Nothing waiting to ship.</td></tr>")

        warn = ""
        if orphan:
            warn = ("<div style='background:#FBEDEA;border:1px solid #E7C3B9;"
                    "color:#8A2C10;padding:14px;border-radius:10px;margin-bottom:18px'>"
                    "<strong>These products are on sale with no supplier — a customer "
                    "can pay and nothing will ship:</strong><br>"
                    + ", ".join(p["name"] for p in orphan) +
                    "<br><button onclick='enforce()' style='background:#A32020;color:#fff;"
                    "border:0;border-radius:7px;padding:8px 14px;font-weight:700;"
                    "cursor:pointer;margin-top:10px'>Take them off sale</button></div>")

        return (
            "<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>Despatch · crittr</title></head>"
            "<body style=\"margin:0;background:#FDFBF5;color:#1C2A1F;font:16px/1.55 "
            "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif\">"
            "<div style='max-width:940px;margin:0 auto;padding:32px 20px 64px'>"
            "<h1 style='font-size:28px;margin:0 0 6px'>Despatch</h1>"
            "<p style='color:#6E7D70;margin:0 0 22px'>Paid orders waiting on a supplier. "
            "Enter a tracking number to close one and email the customer.</p>"
            f"{warn}"
            "<table style='width:100%;border-collapse:collapse;background:#fff;"
            "border:1px solid #DFE5DB;border-radius:12px'>"
            "<tr style='color:#6E7D70;font-size:12px;text-transform:uppercase'>"
            "<th style='text-align:left;padding:8px 10px'>Order</th>"
            "<th style='text-align:left;padding:8px 10px'>Supplier</th>"
            "<th style='text-align:left;padding:8px 10px'>Status</th>"
            "<th style='text-align:left;padding:8px 10px'>Mark despatched</th></tr>"
            + rows + "</table><div id=msg style='margin-top:14px'></div>"
            "<script>"
            "async function ship(id){"
            " const c=document.getElementById('c'+id).value,"
            "       t=document.getElementById('t'+id).value;"
            " const r=await fetch('/api/admin/fulfilments/'+id+'/shipped',{method:'POST',"
            "  headers:{'Content-Type':'application/json'},"
            "  body:JSON.stringify({carrier:c,tracking:t})});"
            " document.getElementById('msg').textContent = r.ok"
            "  ? 'Marked shipped — customer notified.' : 'Could not update.';"
            " if(r.ok) setTimeout(()=>location.reload(),700);}"
            "async function enforce(){"
            " const r=await fetch('/api/admin/enforce_stock',{method:'POST'});"
            " const j=await r.json();"
            " document.getElementById('msg').textContent="
            "  (j.taken_off_sale||0)+' product(s) taken off sale.';"
            " setTimeout(()=>location.reload(),700);}"
            "</script></div></body></html>")

    @app.route("/api/orders/<int:order_id>/status", methods=["GET"])
    def api_order_status(order_id):
        """The customer's own order only."""
        uid = session.get("user_id")
        o = q1("SELECT user_id FROM orders WHERE id=%s", (order_id,))
        if not o:
            return jsonify({"error": "no such order"}), 404
        if not uid or o["user_id"] != uid:
            return jsonify({"error": "not your order"}), 403
        return jsonify(order_status(q, q1, order_id))


def _ser(obj):
    if obj is None:
        return None
    return {k: (v if isinstance(v, (int, float, bool, type(None))) else str(v))
            for k, v in dict(obj).items()}
