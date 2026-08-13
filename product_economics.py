"""crittr.ai — what each product actually earns, and whether it can afford a vet's share.

WHY THIS EXISTS. crittr promises partner practices a percentage of what their clients buy.
Until now nothing in the system knew what a product COST, so that percentage was a number
picked in a meeting rather than one checked against a margin. This is the screen that makes
it checkable: enter what you pay for a thing, and it tells you what is left after the
clinic takes its cut.

THE TWO FACTS IT SURFACES, both of which were invisible before:

  1. WHICH PRODUCTS CAN EVEN EARN A SHARE. A product with an amazon_url renders a "Buy now"
     button that sends the customer to Amazon. No crittr order is created, so attribution
     never runs and the practice earns nothing on it — no matter what the rate is. Twelve
     of the thirteen products are in that state. Marking them ineligible does not change
     the money; it makes the truth visible instead of leaving a clinic to discover it.
  2. WHETHER THE RATE IS AFFORDABLE. Once cost is known, "10% to the vet" becomes a
     concrete number against a concrete margin. If the share exceeds the margin, this says
     so in red rather than letting it be found in a month-end reconciliation.

IT NEVER CHANGES A RATE BY ITSELF. Rates are frozen onto attribution rows at sale time and
quoted per practice; this screen is for deciding, not for retroactively re-pricing anything
that already happened.
"""
import os
import logging

from flask import request, jsonify

log = logging.getLogger("crittr.economics")


def _rate():
    return int(os.environ.get("CRITTR_REV_SHARE_PCT",
                              os.environ.get("CRITTR_VET_REV_SHARE_PCT", "10")))


def product_economics(q):
    """Every product with its margin and what a vet's share would cost against it."""
    rows = q("""SELECT id, name, price_cents, cost_cents, rev_share_eligible,
                       amazon_url, requires_rx, in_stock
                FROM products ORDER BY name""") or []
    rate = _rate()
    out = []
    for r in rows:
        r = dict(r)
        price = int(r.get("price_cents") or 0)
        cost = r.get("cost_cents")
        # The sale PATH is what decides whether a share is even possible. An affiliate
        # link means the customer leaves; there is no crittr order to attribute.
        affiliate = bool(r.get("amazon_url"))
        share = int(round(price * rate / 100.0))
        margin = (price - int(cost)) if cost is not None else None
        left = (margin - share) if margin is not None else None
        out.append({
            "id": r["id"], "name": r["name"],
            "price_cents": price,
            "cost_cents": cost,
            "margin_cents": margin,
            "share_cents": share,
            "after_share_cents": left,
            "path": "affiliate" if affiliate else "crittr_checkout",
            "rev_share_eligible": bool(r.get("rev_share_eligible")),
            "can_actually_earn": (not affiliate) and bool(r.get("rev_share_eligible")),
            "loss_making": (left is not None and left < 0),
            "requires_rx": bool(r.get("requires_rx")),
        })
    earning = [p for p in out if p["can_actually_earn"]]
    costed = [p for p in out if p["cost_cents"] is not None]
    return {
        "rate_pct": rate,
        "products": out,
        "summary": {
            "total": len(out),
            "can_actually_earn_a_share": len(earning),
            "affiliate_only": len([p for p in out if p["path"] == "affiliate"]),
            "with_cost_recorded": len(costed),
            "loss_making_at_current_rate": len([p for p in out if p["loss_making"]]),
        },
    }


def set_economics(q, q1, product_id, *, cost_cents=None, eligible=None):
    """Record what a product costs and whether it may earn a share."""
    if cost_cents is not None:
        try:
            cost_cents = int(cost_cents)
        except (TypeError, ValueError):
            return None, "cost must be a whole number of cents"
        if cost_cents < 0:
            return None, "cost cannot be negative"
        q("UPDATE products SET cost_cents=%s WHERE id=%s", (cost_cents, product_id),
          fetch=False)
    if eligible is not None:
        q("UPDATE products SET rev_share_eligible=%s WHERE id=%s",
          (bool(eligible), product_id), fetch=False)
    return q1("""SELECT id, name, price_cents, cost_cents, rev_share_eligible
                 FROM products WHERE id=%s""", (product_id,)), ""


def sync_affiliate_eligibility(q):
    """Mark every affiliate-linked product ineligible, because it cannot earn anyway.

    Idempotent. This is bookkeeping catching up with reality rather than a policy change:
    an affiliate product never creates a crittr order, so it never reached attribution in
    the first place. Making it explicit means the vet-facing catalogue can say which items
    earn, instead of a clinic finding out from a statement.
    """
    rows = q("""UPDATE products SET rev_share_eligible = FALSE
                WHERE amazon_url IS NOT NULL AND amazon_url <> ''
                  AND rev_share_eligible IS DISTINCT FROM FALSE
                RETURNING id, name""") or []
    if rows:
        log.info("[economics] marked %s affiliate products ineligible", len(rows))
    return [dict(r) for r in rows]


def _money(c):
    return "—" if c is None else f"${c / 100:,.2f}"


def register_economics_routes(app, q, q1, admin_required):

    @app.route("/api/admin/products/economics", methods=["GET"])
    @admin_required
    def api_product_economics():
        return jsonify(product_economics(q))

    @app.route("/api/admin/products/<int:product_id>/economics", methods=["POST"])
    @admin_required
    def api_set_economics(product_id):
        d = request.get_json(silent=True) or {}
        row, why = set_economics(q, q1, product_id,
                                 cost_cents=d.get("cost_cents"),
                                 eligible=d.get("rev_share_eligible"))
        if not row:
            return jsonify({"error": why}), 400
        return jsonify({"ok": True, "product": {k: (v if isinstance(v, (int, bool, type(None)))
                                                    else str(v))
                                                for k, v in dict(row).items()}})

    @app.route("/api/admin/products/sync_affiliate_eligibility", methods=["POST"])
    @admin_required
    def api_sync_affiliate():
        changed = sync_affiliate_eligibility(q)
        return jsonify({"ok": True, "marked_ineligible": len(changed),
                        "products": [c["name"] for c in changed]})

    @app.route("/admin/products", methods=["GET"])
    @admin_required
    def admin_products_page():
        e = product_economics(q)
        s = e["summary"]
        rate = e["rate_pct"]

        rows = []
        for p in e["products"]:
            if p["path"] == "affiliate":
                path = ("<span style='background:#F4F1F1;color:#8A7C7C;font-size:11px;"
                        "font-weight:700;padding:3px 8px;border-radius:99px'>AMAZON</span>")
                note = "customer leaves — no crittr order, cannot earn"
            else:
                path = ("<span style='background:#EAF5E9;color:#2D4A30;font-size:11px;"
                        "font-weight:700;padding:3px 8px;border-radius:99px'>CRITTR</span>")
                note = ""
            if p["cost_cents"] is None:
                margin = "<span style='color:#B4541F'>cost not set</span>"
            elif p["loss_making"]:
                margin = (f"<span style='color:#A32020;font-weight:700'>"
                          f"{_money(p['margin_cents'])} − {_money(p['share_cents'])} = "
                          f"{_money(p['after_share_cents'])}</span>")
                note = f"the {rate}% share exceeds the margin"
            else:
                margin = (f"{_money(p['margin_cents'])} − {_money(p['share_cents'])} = "
                          f"<strong>{_money(p['after_share_cents'])}</strong>")
            rows.append(
                f"<tr><td style='padding:10px 12px;border-top:1px solid #DFE5DB'>"
                f"{p['name']}<div style='color:#6E7D70;font-size:12px'>{note}</div></td>"
                f"<td style='padding:10px 12px;border-top:1px solid #DFE5DB'>{path}</td>"
                f"<td style='padding:10px 12px;border-top:1px solid #DFE5DB'>"
                f"{_money(p['price_cents'])}</td>"
                f"<td style='padding:10px 12px;border-top:1px solid #DFE5DB'>"
                f"<input data-id='{p['id']}' class='cost' type='number' step='0.01' "
                f"value='{'' if p['cost_cents'] is None else p['cost_cents']/100:}' "
                f"placeholder='—' style='width:90px;padding:6px 8px;border:1px solid "
                f"#DFE5DB;border-radius:7px'></td>"
                f"<td style='padding:10px 12px;border-top:1px solid #DFE5DB;font-size:14px'>"
                f"{margin}</td>"
                f"<td style='padding:10px 12px;border-top:1px solid #DFE5DB'>"
                f"<input data-id='{p['id']}' class='elig' type='checkbox' "
                f"{'checked' if p['rev_share_eligible'] else ''}></td></tr>")

        warn = ""
        if s["can_actually_earn_a_share"] <= 1:
            warn = (
                "<div style='background:#FBEDEA;border:1px solid #E7C3B9;color:#8A2C10;"
                "padding:14px;border-radius:10px;margin-bottom:18px'>"
                f"<strong>Only {s['can_actually_earn_a_share']} product can earn a "
                f"practice anything.</strong> {s['affiliate_only']} of {s['total']} send "
                "the customer to Amazon, where no crittr order exists to attribute. The "
                "revenue share is built and correct — the catalogue is what limits it."
                "</div>")

        return (
            "<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>Product economics · crittr</title></head>"
            "<body style=\"margin:0;background:#FDFBF5;color:#1C2A1F;font:16px/1.55 "
            "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif\">"
            "<div style='max-width:1000px;margin:0 auto;padding:32px 20px 64px'>"
            "<h1 style='font-size:28px;margin:0 0 6px'>Product economics</h1>"
            f"<p style='color:#6E7D70;margin:0 0 22px'>What each product costs, and what "
            f"is left after a partner practice takes {rate}%.</p>"
            f"{warn}"
            f"<p style='color:#6E7D70;font-size:14px'>{s['with_cost_recorded']} of "
            f"{s['total']} products have a cost recorded · "
            f"{s['loss_making_at_current_rate']} loss-making at {rate}%</p>"
            "<table style='width:100%;border-collapse:collapse;background:#fff;"
            "border:1px solid #DFE5DB;border-radius:12px;margin-top:8px'>"
            "<tr style='color:#6E7D70;font-size:12px;text-transform:uppercase;"
            "letter-spacing:.04em'>"
            "<th style='text-align:left;padding:8px 12px'>Product</th>"
            "<th style='text-align:left;padding:8px 12px'>Sold via</th>"
            "<th style='text-align:left;padding:8px 12px'>Retail</th>"
            "<th style='text-align:left;padding:8px 12px'>Our cost</th>"
            f"<th style='text-align:left;padding:8px 12px'>Margin − {rate}% share</th>"
            "<th style='text-align:left;padding:8px 12px'>Can earn</th></tr>"
            + "".join(rows) + "</table>"
            "<button id=save style='background:#527E54;color:#fff;border:0;"
            "border-radius:9px;padding:13px 20px;font:inherit;font-weight:700;"
            "cursor:pointer;margin-top:20px'>Save changes</button> "
            "<button id=sync style='background:#fff;color:#3E6340;border:1px solid "
            "#DFE5DB;border-radius:9px;padding:13px 20px;font:inherit;font-weight:700;"
            "cursor:pointer;margin-top:20px'>Mark all Amazon products ineligible</button>"
            "<div id=msg style='margin-top:14px'></div>"
            "<script>"
            "document.getElementById('save').onclick=async()=>{"
            " const m=document.getElementById('msg'); m.textContent='Saving…';"
            " const ids=new Set(); const body={};"
            " document.querySelectorAll('input.cost').forEach(i=>{ids.add(i.dataset.id);});"
            " document.querySelectorAll('input.elig').forEach(i=>{ids.add(i.dataset.id);});"
            " let n=0;"
            " for(const id of ids){"
            "  const c=document.querySelector('input.cost[data-id=\"'+id+'\"]');"
            "  const e=document.querySelector('input.elig[data-id=\"'+id+'\"]');"
            "  const payload={rev_share_eligible:e.checked};"
            "  if(c.value!=='') payload.cost_cents=Math.round(parseFloat(c.value)*100);"
            "  const r=await fetch('/api/admin/products/'+id+'/economics',{method:'POST',"
            "   headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});"
            "  if(r.ok) n++;"
            " }"
            " m.textContent=n+' products updated.'; setTimeout(()=>location.reload(),700);"
            "};"
            "document.getElementById('sync').onclick=async()=>{"
            " const r=await fetch('/api/admin/products/sync_affiliate_eligibility',"
            "  {method:'POST'}); const j=await r.json();"
            " document.getElementById('msg').textContent="
            "  (j.marked_ineligible||0)+' affiliate products marked ineligible.';"
            " setTimeout(()=>location.reload(),700);"
            "};"
            "</script></div></body></html>")
