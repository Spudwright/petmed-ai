"""crittr.ai — Email service via Resend. Voss-toned templates."""
import os
import json
import html as _html


def _fmt_price(cents):
    return f"${cents/100:.2f}" if cents is not None else "$0.00"


def _render_order_html(name, order_id, items, subtotal_cents, tax_cents, shipping_cents, total_cents, shipping_address, app_url):
    first_name = (name or "friend").split()[0] if name else "friend"
    items_rows = ""
    for it in items:
        qty = it.get("quantity", 1)
        pname = _html.escape(it.get("name", "item"))
        line_total = _fmt_price(int(it.get("price_cents", 0)) * qty)
        items_rows += f'<tr><td style="padding:12px 0;border-bottom:1px solid #E4EFE2;font-size:15px;color:#1C2A1F;">{pname}<br><span style="color:#6E7D70;font-size:13px">Qty {qty}</span></td><td style="padding:12px 0;border-bottom:1px solid #E4EFE2;text-align:right;font-size:15px;color:#1C2A1F;">{line_total}</td></tr>'
    shipping_block = ""
    if shipping_address:
        sa = shipping_address
        parts = [sa.get("line1"), sa.get("line2"), ", ".join(filter(None, [sa.get("city"), sa.get("state"), sa.get("postal_code")])), sa.get("country")]
        clean = [p for p in parts if p]
        if clean:
            addr_html = "<br>".join(_html.escape(p) for p in clean)
            shipping_block = f'<div style="margin-top:24px;padding:16px;background:#F2F7F1;border-radius:12px"><div style="font-weight:600;color:#2D4A30;font-size:14px;margin-bottom:6px">Shipping to</div><div style="color:#374C3C;font-size:14px;line-height:1.5">{addr_html}</div></div>'
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Your crittr order is confirmed</title></head><body style="margin:0;padding:0;background:#FDFBF5;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1C2A1F;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#FDFBF5;padding:40px 16px"><tr><td align="center"><table role="presentation" width="560" cellpadding="0" cellspacing="0" border="0" style="max-width:560px;background:#ffffff;border-radius:16px;overflow:hidden;border:1px solid #DFE5DB"><tr><td style="padding:32px 40px 0 40px"><div style="font-family:Georgia,serif;font-size:22px;font-weight:600;color:#2D4A30;letter-spacing:-0.02em"><span style="display:inline-block;width:12px;height:12px;border-radius:50%;background:#6B9E6B;box-shadow:0 0 0 4px #E4EFE2;vertical-align:middle;margin-right:8px"></span>crittr</div></td></tr><tr><td style="padding:32px 40px 8px 40px"><h1 style="font-family:Georgia,serif;font-size:30px;font-weight:500;line-height:1.15;color:#1F3221;margin:0 0 16px 0">It sounds like you and your critter are about to be in good hands, {_html.escape(first_name)}.</h1><p style="font-size:16px;line-height:1.55;color:#374C3C;margin:0 0 8px 0">Your order is confirmed. We&apos;re on it. I&apos;ll be honest with you — the next moment where you&apos;ll really feel this is when the package shows up at your door. Until then, you can stop thinking about it. We&apos;ve got it.</p></td></tr><tr><td style="padding:24px 40px 0 40px"><div style="font-size:12px;letter-spacing:0.14em;text-transform:uppercase;color:#527E54;font-weight:600;margin-bottom:8px">What&apos;s on the way</div><div style="color:#6E7D70;font-size:14px;margin-bottom:12px">Order #{order_id}</div><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{items_rows}<tr><td style="padding:12px 0;font-size:14px;color:#6E7D70">Subtotal</td><td style="padding:12px 0;text-align:right;font-size:14px;color:#6E7D70">{_fmt_price(subtotal_cents)}</td></tr><tr><td style="padding:4px 0;font-size:14px;color:#6E7D70">Tax</td><td style="padding:4px 0;text-align:right;font-size:14px;color:#6E7D70">{_fmt_price(tax_cents)}</td></tr><tr><td style="padding:4px 0 12px 0;font-size:14px;color:#6E7D70">Shipping</td><td style="padding:4px 0 12px 0;text-align:right;font-size:14px;color:#6E7D70">{_fmt_price(shipping_cents)}</td></tr><tr><td style="padding:12px 0;border-top:1px solid #DFE5DB;font-family:Georgia,serif;font-size:18px;font-weight:600;color:#1F3221">Total</td><td style="padding:12px 0;border-top:1px solid #DFE5DB;text-align:right;font-family:Georgia,serif;font-size:18px;font-weight:600;color:#1F3221">{_fmt_price(total_cents)}</td></tr></table>{shipping_block}</td></tr><tr><td style="padding:32px 40px 0 40px"><div style="font-size:12px;letter-spacing:0.14em;text-transform:uppercase;color:#527E54;font-weight:600;margin-bottom:8px">What happens now</div><p style="font-size:15px;line-height:1.6;color:#374C3C;margin:0 0 16px 0">We pack your order within one business day. Tracking lands in your inbox the moment it ships. If there&apos;s an Rx item in your order, our licensed pharmacy partner reviews it first — usually within 24 hours. You&apos;ll hear from us either way.</p><p style="font-size:15px;line-height:1.6;color:#374C3C;margin:0 0 16px 0">If anything feels off — wrong item, something damaged, a question that&apos;s been nagging you — just reply to this email. A real human on our team will read it and get back to you within a day. Promise.</p></td></tr><tr><td style="padding:24px 40px 0 40px"><a href="{app_url}/#/account" style="display:inline-block;background:#527E54;color:#ffffff;text-decoration:none;padding:14px 24px;border-radius:999px;font-weight:600;font-size:15px">View your orders</a></td></tr><tr><td style="padding:40px 40px 16px 40px"><p style="font-size:15px;line-height:1.6;color:#374C3C;margin:0">Thank you for trusting us with your critter.<br>— The crittr team</p></td></tr><tr><td style="padding:24px 40px 32px 40px;border-top:1px solid #DFE5DB"><p style="font-size:12px;line-height:1.5;color:#89968A;margin:0">crittr.ai · Licensed veterinary pharmacy · USA<br>You&apos;re receiving this because you placed an order with crittr. Questions? Just reply.</p></td></tr></table></td></tr></table></body></html>'''


def _render_order_text(name, order_id, items, subtotal_cents, tax_cents, shipping_cents, total_cents, shipping_address, app_url):
    first_name = (name or "friend").split()[0] if name else "friend"
    lines = [
        f"It sounds like you and your critter are about to be in good hands, {first_name}.", "",
        "Your order is confirmed. We're on it. I'll be honest with you — the next moment where",
        "you'll really feel this is when the package shows up at your door. Until then, you can",
        "stop thinking about it. We've got it.", "",
        f"ORDER #{order_id}", "-" * 40,
    ]
    for it in items:
        qty = it.get("quantity", 1)
        line_total = _fmt_price(int(it.get("price_cents", 0)) * qty)
        lines.append(f"  {it.get('name','item')} x{qty}  {line_total}")
    lines += [
        "",
        f"Subtotal: {_fmt_price(subtotal_cents)}",
        f"Tax:      {_fmt_price(tax_cents)}",
        f"Shipping: {_fmt_price(shipping_cents)}",
        f"Total:    {_fmt_price(total_cents)}", "",
    ]
    if shipping_address:
        sa = shipping_address
        parts = [sa.get("line1"), sa.get("line2"), ", ".join(filter(None, [sa.get("city"), sa.get("state"), sa.get("postal_code")])), sa.get("country")]
        clean = [p for p in parts if p]
        if clean:
            lines.append("Shipping to:")
            for p in clean:
                lines.append(f"  {p}")
            lines.append("")
    lines += [
        "WHAT HAPPENS NOW", "-" * 40,
        "We pack your order within one business day. Tracking lands in your inbox the moment",
        "it ships. If there's an Rx item in your order, our licensed pharmacy partner reviews",
        "it first — usually within 24 hours. You'll hear from us either way.", "",
        "If anything feels off — wrong item, something damaged, a question that's been nagging",
        "you — just reply to this email. A real human on our team will read it and get back",
        "to you within a day. Promise.", "",
        f"View your orders: {app_url}/#/account", "",
        "Thank you for trusting us with your critter.",
        "— The crittr team", "",
        "---",
        "crittr.ai · Licensed veterinary pharmacy · USA",
        "You're receiving this because you placed an order with crittr. Questions? Just reply.",
    ]
    return "\n".join(lines)


def send_order_confirmation(to_email, name, order_id, items, subtotal_cents, tax_cents, shipping_cents, total_cents, shipping_address=None):
    if not to_email:
        return False
    api_key = os.environ.get("RESEND_API_KEY", "")
    from_email = os.environ.get("FROM_EMAIL", "crittr <hello@crittr.ai>")
    app_url = os.environ.get("APP_URL", "https://crittr.ai").rstrip("/")
    if not api_key:
        import logging
        logging.getLogger(__name__).warning("[emails] RESEND_API_KEY not set — skipping send")
        return False
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except Exception:
            items = []
    html_body = _render_order_html(name, order_id, items or [], subtotal_cents or 0, tax_cents or 0, shipping_cents or 0, total_cents or 0, shipping_address, app_url)
    text_body = _render_order_text(name, order_id, items or [], subtotal_cents or 0, tax_cents or 0, shipping_cents or 0, total_cents or 0, shipping_address, app_url)
    try:
        import resend
        resend.api_key = api_key
        resend.Emails.send({
            "from": from_email,
            "to": [to_email],
            "subject": f"Your crittr order #{order_id} is confirmed",
            "html": html_body,
            "text": text_body,
            "reply_to": os.environ.get("REPLY_TO_EMAIL", from_email),
        })
        return True
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"[emails] send failed: {e}")
        return False


def _render_abandoned_cart_html(name, items, subtotal_cents, credit_balance_cents, checkout_url, app_url):
    first_name = (name or "friend").split()[0] if name else "friend"
    items_rows = ""
    for it in items or []:
        qty = it.get("quantity", 1)
        pname = _html.escape(it.get("name", "item"))
        line_total = _fmt_price(int(it.get("price_cents", 0)) * qty)
        items_rows += f'<tr><td style="padding:12px 0;border-bottom:1px solid #E4EFE2;font-size:15px;color:#1C2A1F;">{pname}<br><span style="color:#6E7D70;font-size:13px">Qty {qty}</span></td><td style="padding:12px 0;border-bottom:1px solid #E4EFE2;text-align:right;font-size:15px;color:#1C2A1F;">{line_total}</td></tr>'
    credit_block = ""
    if credit_balance_cents and credit_balance_cents > 0:
        credit_block = f'<div style="margin-top:20px;padding:16px 20px;background:#F2F7F1;border-radius:12px;border:1px solid #DFE5DB"><div style="font-size:13px;letter-spacing:0.08em;text-transform:uppercase;color:#527E54;font-weight:600;margin-bottom:6px">Waiting for you</div><div style="font-size:18px;font-family:Georgia,serif;color:#1F3221;font-weight:600">{_fmt_price(credit_balance_cents)} in crittr credit</div><div style="font-size:14px;color:#374C3C;margin-top:4px">Applied automatically at checkout.</div></div>'
    cta = checkout_url or f"{app_url}/#/cart"
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>You left something for your critter</title></head><body style="margin:0;padding:0;background:#FDFBF5;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1C2A1F;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#FDFBF5;padding:40px 16px"><tr><td align="center"><table role="presentation" width="560" cellpadding="0" cellspacing="0" border="0" style="max-width:560px;background:#ffffff;border-radius:16px;overflow:hidden;border:1px solid #DFE5DB"><tr><td style="padding:32px 40px 0 40px"><div style="font-family:Georgia,serif;font-size:22px;font-weight:600;color:#2D4A30;letter-spacing:-0.02em"><span style="display:inline-block;width:12px;height:12px;border-radius:50%;background:#6B9E6B;box-shadow:0 0 0 4px #E4EFE2;vertical-align:middle;margin-right:8px"></span>crittr</div></td></tr><tr><td style="padding:32px 40px 8px 40px"><h1 style="font-family:Georgia,serif;font-size:30px;font-weight:500;line-height:1.15;color:#1F3221;margin:0 0 16px 0">It sounds like you got pulled away, {_html.escape(first_name)}.</h1><p style="font-size:16px;line-height:1.55;color:#374C3C;margin:0 0 8px 0">No judgment. Life does that. I just wanted to let you know your cart is still here, waiting. One click and we pick up right where you left off — we&apos;ve got your critter&apos;s back.</p></td></tr><tr><td style="padding:24px 40px 0 40px"><div style="font-size:12px;letter-spacing:0.14em;text-transform:uppercase;color:#527E54;font-weight:600;margin-bottom:8px">Still in your cart</div><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{items_rows}<tr><td style="padding:12px 0;font-family:Georgia,serif;font-size:18px;font-weight:600;color:#1F3221">Subtotal</td><td style="padding:12px 0;text-align:right;font-family:Georgia,serif;font-size:18px;font-weight:600;color:#1F3221">{_fmt_price(subtotal_cents)}</td></tr></table>{credit_block}</td></tr><tr><td style="padding:32px 40px 0 40px"><a href="{cta}" style="display:inline-block;background:#527E54;color:#ffffff;text-decoration:none;padding:14px 28px;border-radius:999px;font-weight:600;font-size:15px">Pick up where you left off</a></td></tr><tr><td style="padding:32px 40px 0 40px"><p style="font-size:15px;line-height:1.6;color:#374C3C;margin:0">If something gave you pause — price, timing, a question about your critter — just reply to this email. A real human on our team will read it. No scripts.</p></td></tr><tr><td style="padding:40px 40px 16px 40px"><p style="font-size:15px;line-height:1.6;color:#374C3C;margin:0">Whenever you&apos;re ready.<br>— The crittr team</p></td></tr><tr><td style="padding:24px 40px 32px 40px;border-top:1px solid #DFE5DB"><p style="font-size:12px;line-height:1.5;color:#89968A;margin:0">crittr.ai · Licensed veterinary pharmacy · USA<br>You&apos;re receiving this because you started a checkout with crittr. Not interested? Just reply and we&apos;ll stop.</p></td></tr></table></td></tr></table></body></html>'''


def _render_abandoned_cart_text(name, items, subtotal_cents, credit_balance_cents, checkout_url, app_url):
    first_name = (name or "friend").split()[0] if name else "friend"
    lines = [
        f"It sounds like you got pulled away, {first_name}.", "",
        "No judgment. Life does that. I just wanted to let you know your cart is still here,",
        "waiting. One click and we pick up right where you left off — we've got your critter's back.", "",
        "STILL IN YOUR CART", "-" * 40,
    ]
    for it in items or []:
        qty = it.get("quantity", 1)
        line_total = _fmt_price(int(it.get("price_cents", 0)) * qty)
        lines.append(f"  {it.get('name','item')} x{qty}  {line_total}")
    lines += ["", f"Subtotal: {_fmt_price(subtotal_cents)}", ""]
    if credit_balance_cents and credit_balance_cents > 0:
        lines += [
            f"WAITING FOR YOU: {_fmt_price(credit_balance_cents)} in crittr credit",
            "Applied automatically at checkout.", "",
        ]
    cta = checkout_url or f"{app_url}/#/cart"
    lines += [
        f"Pick up where you left off: {cta}", "",
        "If something gave you pause — price, timing, a question about your critter — just",
        "reply to this email. A real human on our team will read it. No scripts.", "",
        "Whenever you're ready.",
        "— The crittr team", "",
        "---",
        "crittr.ai · Licensed veterinary pharmacy · USA",
        "You're receiving this because you started a checkout with crittr. Not interested? Just reply and we'll stop.",
    ]
    return "\n".join(lines)


def send_abandoned_cart_email(to_email, name, items, subtotal_cents, credit_balance_cents=0, checkout_url=None):
    if not to_email:
        return False
    api_key = os.environ.get("RESEND_API_KEY", "")
    from_email = os.environ.get("FROM_EMAIL", "crittr <hello@crittr.ai>")
    app_url = os.environ.get("APP_URL", "https://crittr.ai").rstrip("/")
    if not api_key:
        import logging
        logging.getLogger(__name__).warning("[emails] RESEND_API_KEY not set — skipping abandoned cart send")
        return False
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except Exception:
            items = []
    html_body = _render_abandoned_cart_html(name, items or [], subtotal_cents or 0, credit_balance_cents or 0, checkout_url, app_url)
    text_body = _render_abandoned_cart_text(name, items or [], subtotal_cents or 0, credit_balance_cents or 0, checkout_url, app_url)
    try:
        import resend
        resend.api_key = api_key
        resend.Emails.send({
            "from": from_email,
            "to": [to_email],
            "subject": "You left something for your critter",
            "html": html_body,
            "text": text_body,
            "reply_to": os.environ.get("REPLY_TO_EMAIL", from_email),
        })
        return True
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"[emails] abandoned cart send failed: {e}")
        return False
