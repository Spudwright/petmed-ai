"""crittr.ai — is this deployment actually able to do the things it claims?

WHY THIS EXISTS. Every silent failure crittr has had shares a shape: a missing environment
variable turns a feature into a no-op that still returns HTTP 200. Invitations report
`{"ok": true, "sent": 0}` with no RESEND_API_KEY. The chart assistant refuses politely with
no ANTHROPIC_API_KEY. Attribution credits nobody if the Stripe webhook secret is wrong, so
the webhook never arrives at all. In every case the API looks healthy and the business
outcome silently does not happen — which is the worst failure mode there is, because you
only find out when a clinic asks where their clients went.

So this answers one question honestly: what would break RIGHT NOW, and how would I know?

IT NEVER RETURNS A SECRET. Only whether one is present, and — where the provider allows a
free read-only call — whether it actually WORKS. "Set" and "valid" are different questions
and a key that is set but revoked is exactly the case that costs you a pilot.
"""
import os
import logging

from flask import jsonify

log = logging.getLogger("crittr.readiness")


def _probe_resend():
    """Does the Resend key actually authenticate? Uses a read-only endpoint, sends nothing.

    Listing domains costs nothing and delivers no mail, so this is safe to call from a
    dashboard. A 401 here is the difference between "we configured it" and "it works".
    """
    key = os.environ.get("RESEND_API_KEY", "")
    if not key:
        return {"set": False, "valid": None,
                "impact": "client invitations and clawback notices silently do nothing — "
                          "the API still returns ok:true and sent:0"}
    try:
        import urllib.request
        req = urllib.request.Request(
            "https://api.resend.com/domains",
            headers={"Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=8) as r:
            ok = 200 <= r.status < 300
        return {"set": True, "valid": ok,
                "impact": "" if ok else "the key is set but Resend rejected it"}
    except Exception as e:                                  # noqa: BLE001
        code = getattr(e, "code", None)
        if code == 401:
            return {"set": True, "valid": False,
                    "impact": "the key is SET but Resend rejected it (HTTP 401) — it is "
                              "invalid or revoked, and invitations will silently fail"}
        if code == 403:
            # NOT a failure. Resend keys come in "Full access" and "Sending access"
            # flavours, and a sending-only key can post an email perfectly well while
            # being forbidden from listing domains — which is all this probe reads. The
            # first version of this treated 403 as broken and would have sent someone off
            # to regenerate a key that was working correctly.
            return {"set": True, "valid": None, "scope": "sending-only",
                    "impact": "the key is valid but scoped to sending only, so this check "
                              "cannot read domains. Email should work — confirm by sending "
                              "one real invitation."}
        return {"set": True, "valid": None,
                "impact": f"could not reach Resend to check ({e}); the key is set"}


def _probe_connect():
    """Is Connect actually switched on for this account, and which account is it?

    Listing connected accounts is a read-only call that costs nothing and creates nothing.
    If Connect is not enabled Stripe refuses it, which is the difference between "we clicked
    through the dashboard" and "a practice can actually onboard". The account id is
    reported too, because the expensive mistake here is enabling Connect on one Stripe
    account while crittr's key belongs to another.
    """
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not key:
        return {"enabled": None, "reason": "no STRIPE_SECRET_KEY"}
    try:
        import stripe as _s
        _s.api_key = key
        acct = _s.Account.retrieve()
        _s.Account.list(limit=1)
        return {"enabled": True, "account_id": getattr(acct, "id", None),
                "account_name": (getattr(acct, "settings", None) or {}).get(
                    "dashboard", {}).get("display_name")
                if isinstance(getattr(acct, "settings", None), dict) else None,
                "charges_enabled": bool(getattr(acct, "charges_enabled", False)),
                "payouts_enabled": bool(getattr(acct, "payouts_enabled", False))}
    except Exception as e:                                  # noqa: BLE001
        msg = str(e)[:220]
        looks_off = ("Connect" in msg or "not enabled" in msg or "signed up" in msg)
        return {"enabled": False if looks_off else None, "error": msg,
                "impact": ("no practice can connect a bank account, so neither consult "
                           "fees nor product payouts can move money")}


def _probe_stripe():
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    hook = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    out = {"secret_key_set": bool(key), "webhook_secret_set": bool(hook),
           "livemode": key.startswith("sk_live_") if key else None,
           "connect": _probe_connect()}
    if not hook:
        out["impact"] = ("no webhook secret: crittr never receives "
                         "checkout.session.completed, so orders are never marked paid AND "
                         "no vet is ever credited")
    elif not key:
        out["impact"] = "no secret key: checkout cannot be created at all"
    return out


def _probe_llm():
    """Actually call each provider. "Set" and "answers" are different questions.

    This is the third time that distinction has mattered — a Resend key that was set but
    scoped, a Connect setting that looked configured, and now VET AI failing on the live
    site while readiness cheerfully reported openai_set: true. A key being present says
    nothing about whether it has credit, whether the model name is still valid, or whether
    the account is in good standing.

    Costs a handful of tokens per call, on an admin-only page.
    """
    out = {"anthropic_set": bool(os.environ.get("ANTHROPIC_API_KEY")),
           "openai_set": bool(os.environ.get("OPENAI_API_KEY"))}

    if out["anthropic_set"]:
        try:
            import anthropic
            c = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], timeout=15.0)
            c.messages.create(model=os.environ.get("ANTHROPIC_MODEL",
                                                   "claude-haiku-4-5-20251001"),
                              max_tokens=5, messages=[{"role": "user", "content": "hi"}])
            out["anthropic"] = {"answers": True}
        except Exception as e:                              # noqa: BLE001
            out["anthropic"] = {"answers": False, "error": str(e)[:200]}

    if out["openai_set"]:
        try:
            import openai
            c = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=15.0)
            model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
            c.chat.completions.create(model=model, max_tokens=5,
                                      messages=[{"role": "user", "content": "hi"}])
            out["openai"] = {"answers": True, "model": model}
        except Exception as e:                              # noqa: BLE001
            out["openai"] = {"answers": False,
                             "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
                             "error": str(e)[:200]}

    working = [p for p in ("anthropic", "openai") if (out.get(p) or {}).get("answers")]
    out["working"] = working
    if not working:
        out["impact"] = ("VET AI, the triage and the chart assistant all fail — the site "
                         "shows 'I'm having trouble right now'. This is user-visible.")
    return out


def _probe_turnstile():
    """The bot gate's two keys are a pair. Half a pair rejects every real user.

    TURNSTILE_SECRET_KEY makes the server demand a token; TURNSTILE_SITE_KEY is what lets
    the browser make one. Set only the secret and the front end can never produce a token,
    so every visitor is told "Verification required" — which is what happened to VET AI on
    the live site and read as the AI being broken.
    """
    site = (os.environ.get("TURNSTILE_SITE_KEY") or "").strip()
    secret = (os.environ.get("TURNSTILE_SECRET_KEY") or "").strip()

    # HAVING THE KEY IS NOT THE SAME AS SHIPPING IT. The site key only reaches the browser
    # as a <meta> tag injected into the served HTML. That injection silently no-opped for
    # weeks because its "already present?" guard matched the JavaScript that READS the tag,
    # so the server demanded a token the browser had no way to make and every visitor got
    # 403. Env vars looked perfect throughout. Check what is actually served.
    meta_ok = None
    if site:
        try:
            import app as _app                       # late: avoids a circular import
            meta_ok = getattr(_app, "TURNSTILE_META_OK", None)
        except Exception:                            # noqa: BLE001
            meta_ok = None
        if meta_ok is False:
            return {"site_key_set": True, "secret_key_set": bool(secret),
                    "meta_tag_served": False, "ok": False,
                    "impact": "TURNSTILE_SITE_KEY is set but the meta tag is NOT in the "
                              "served HTML, so the browser cannot produce a token and "
                              "EVERY chat request is rejected with 403 'Verification "
                              "required'. The AI will look completely dead to visitors."}

    if secret and not site:
        return {"site_key_set": False, "secret_key_set": True, "ok": False,
                "impact": "SECRET set without SITE key — the browser cannot produce a "
                          "token, so the bot gate would reject every real visitor. The "
                          "code now fails open, but set TURNSTILE_SITE_KEY to actually "
                          "have bot protection."}
    if site and not secret:
        return {"site_key_set": True, "secret_key_set": False, "ok": True,
                "impact": "widget renders but nothing is verified server-side — harmless, "
                          "just not protecting anything"}
    return {"site_key_set": bool(site), "secret_key_set": bool(secret),
            "meta_tag_served": meta_ok, "ok": True,
            "impact": "" if site else "bot gate disabled (neither key set)"}


def _probe_alerts():
    """Can a budget alert actually REACH anyone?

    The spend governor warns when AI spend is running hot or is on pace to exhaust the
    month early. Those warnings go out over Resend to ALERT_EMAIL. With no destination set
    they are written to the log and nothing else, and a log nobody reads is not monitoring
    — the whole point of the warning is that it arrives before the cap does.

    This is the same silent-failure shape as the Turnstile pair: everything returns 200,
    nothing errors, and the feature simply is not there.
    """
    to = (os.environ.get("ALERT_EMAIL") or os.environ.get("REPLY_TO_EMAIL") or "").strip()
    key = (os.environ.get("RESEND_API_KEY") or "").strip()
    if not to:
        return {"destination_set": False, "ok": False,
                "impact": "no ALERT_EMAIL — AI spend warnings are logged and never sent, "
                          "so the first you would know is /admin/ai-spend or the invoice"}
    if not key:
        return {"destination_set": True, "ok": False,
                "impact": "ALERT_EMAIL is set but RESEND_API_KEY is not, so nothing can "
                          "send the warning"}
    return {"destination_set": True, "ok": True,
            "via": "ALERT_EMAIL" if os.environ.get("ALERT_EMAIL") else "REPLY_TO_EMAIL",
            "impact": ""}


def _probe_admin():
    u, p = os.environ.get("ADMIN_USER"), os.environ.get("ADMIN_PASS")
    return {"set": bool(u and p),
            "impact": "" if (u and p) else
                      "every admin route returns 404 by design — you cannot verify a vet "
                      "or activate a state until these are set"}


def margin_visibility(q):
    """How much of the catalogue can we actually check a revenue-share rate against?

    A rate is a guess until you know what the thing costs. This counts the guessing.
    """
    try:
        row = q("""SELECT COUNT(*) AS total,
                          COUNT(cost_cents) AS costed,
                          COUNT(*) FILTER (WHERE rev_share_eligible) AS eligible
                   FROM products""")
        r = dict(row[0]) if row else {}
        total = int(r.get("total") or 0)
        costed = int(r.get("costed") or 0)
        return {"products": total, "with_cost": costed,
                "share_eligible": int(r.get("eligible") or 0),
                "impact": "" if costed == total else
                          f"{total - costed} of {total} products have no cost recorded, so "
                          f"the revenue share cannot be checked against margin on them"}
    except Exception as e:                                  # noqa: BLE001
        return {"error": str(e)}


def readiness(q=None):
    """The whole picture. Booleans and consequences only — never a secret."""
    resend = _probe_resend()
    stripe_ = _probe_stripe()
    llm = _probe_llm()
    admin = _probe_admin()
    turnstile = _probe_turnstile()
    alerts = _probe_alerts()
    db = bool(os.environ.get("DATABASE_URL"))

    blocking = []
    if not db:
        blocking.append("DATABASE_URL")
    if not stripe_["webhook_secret_set"]:
        blocking.append("STRIPE_WEBHOOK_SECRET")
    if (stripe_.get("connect") or {}).get("enabled") is False:
        blocking.append("STRIPE_CONNECT_NOT_ENABLED")
    if resend["set"] is False or resend.get("valid") is False:
        blocking.append("RESEND_API_KEY")
    if not admin["set"]:
        blocking.append("ADMIN_USER/ADMIN_PASS")
    if not llm.get("working"):
        # User-visible: the chat box on the front page says "I'm having trouble right now".
        blocking.append("NO_WORKING_LLM")
    if not turnstile["ok"]:
        # User-visible and total: every chat message is refused.
        blocking.append("TURNSTILE_MISCONFIGURED")
    if not alerts["ok"]:
        # Not user-visible at all, which is exactly why it belongs here: spend runs hot and
        # nobody is told until someone happens to open the dashboard.
        blocking.append("AI_SPEND_ALERTS_GO_NOWHERE")

    return {
        "ok": not blocking,
        "blocking": blocking,
        "database": {"set": db},
        "email": resend,
        "stripe": stripe_,
        "llm": llm,
        "admin": admin,
        "turnstile": turnstile,
        "spend_alerts": alerts,
        "rev_share_pct": os.environ.get("CRITTR_REV_SHARE_PCT",
                                        os.environ.get("CRITTR_VET_REV_SHARE_PCT", "10")),
        "margin": margin_visibility(q) if q else None,
        "app_url": os.environ.get("APP_URL", "https://crittr.ai"),
    }


def register_readiness(app, admin_required, q=None):
    @app.route("/api/admin/readiness", methods=["GET"])
    @admin_required
    def api_readiness():
        return jsonify(readiness(q))

    @app.route("/admin/readiness", methods=["GET"])
    @admin_required
    def readiness_page():
        r = readiness(q)
        rows = []

        def row(label, ok, detail=""):
            colour = "#2D4A30" if ok else "#8A2C10"
            bg = "#EAF5E9" if ok else "#FBEDEA"
            mark = "OK" if ok else "MISSING"
            rows.append(
                f"<tr><td style='padding:10px 12px;border-top:1px solid #DFE5DB'>{label}"
                f"</td><td style='padding:10px 12px;border-top:1px solid #DFE5DB'>"
                f"<span style='background:{bg};color:{colour};font-weight:700;"
                f"font-size:12px;padding:3px 9px;border-radius:99px'>{mark}</span></td>"
                f"<td style='padding:10px 12px;border-top:1px solid #DFE5DB;color:#6E7D70;"
                f"font-size:14px'>{detail}</td></tr>")

        row("Database", r["database"]["set"])
        em = r["email"]
        row("Email (Resend)",
            em["set"] and em.get("valid") is not False,
            em.get("impact") or ("key set and Resend accepted it"
                                 if em.get("valid") else "key set; not verified"))
        row("Stripe secret key", r["stripe"]["secret_key_set"],
            "live mode" if r["stripe"].get("livemode") else "TEST mode key")
        row("Stripe webhook secret", r["stripe"]["webhook_secret_set"],
            r["stripe"].get("impact", ""))
        _cn = r["stripe"].get("connect") or {}
        row("Stripe Connect",
            _cn.get("enabled") is True,
            (f"account {_cn.get('account_id')} · charges "
             f"{'on' if _cn.get('charges_enabled') else 'OFF'} · payouts "
             f"{'on' if _cn.get('payouts_enabled') else 'OFF'}")
            if _cn.get("enabled") else (_cn.get("impact") or _cn.get("error", "")))
        _l = r["llm"]
        _err = ((_l.get("openai") or {}).get("error")
                or (_l.get("anthropic") or {}).get("error") or "")
        row("VET AI / chat", bool(_l.get("working")),
            (f"answering via {', '.join(_l['working'])}" if _l.get("working")
             else (_err[:150] or _l.get("impact", ""))))
        row("Admin credentials", r["admin"]["set"], r["admin"].get("impact", ""))
        _ts = r.get("turnstile") or {}
        row("Bot gate (Turnstile)", _ts.get("ok", True), _ts.get("impact", ""))
        m = r.get("margin") or {}
        if m and not m.get("error"):
            row("Product cost data",
                m.get("with_cost") == m.get("products"),
                m.get("impact") or "every product has a cost recorded")

        banner = ("<div style='background:#EAF5E9;border:1px solid #A6C9A2;color:#2D4A30;"
                  "padding:14px;border-radius:10px'><strong>Everything needed is "
                  "configured.</strong></div>"
                  if r["ok"] else
                  "<div style='background:#FBEDEA;border:1px solid #E7C3B9;color:#8A2C10;"
                  "padding:14px;border-radius:10px'><strong>Not ready: </strong>"
                  + ", ".join(r["blocking"]) +
                  ". Each of these fails SILENTLY — the API keeps returning 200.</div>")

        return (
            "<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>Readiness · crittr</title></head>"
            "<body style=\"margin:0;background:#FDFBF5;color:#1C2A1F;font:16px/1.55 "
            "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif\">"
            "<div style='max-width:820px;margin:0 auto;padding:32px 20px 64px'>"
            "<h1 style='font-size:28px;margin:0 0 6px'>Deployment readiness</h1>"
            "<p style='color:#6E7D70;margin:0 0 22px'>What would break right now, and how "
            "you would otherwise never notice.</p>"
            f"{banner}"
            "<table style='width:100%;border-collapse:collapse;background:#fff;"
            "border:1px solid #DFE5DB;border-radius:12px;margin-top:18px'>"
            + "".join(rows) + "</table>"
            f"<p style='color:#6E7D70;font-size:14px;margin-top:18px'>Vet revenue share: "
            f"<strong>{r['rev_share_pct']}%</strong> · App URL: {r['app_url']}</p>"
            "<p style='color:#6E7D70;font-size:13px'>No secret is ever shown on this page "
            "— only whether one is present and, for Resend, whether it authenticates.</p>"
            "</div></body></html>")
