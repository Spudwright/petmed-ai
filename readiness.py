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
        if code in (401, 403):
            return {"set": True, "valid": False,
                    "impact": f"the key is SET but Resend rejected it (HTTP {code}) — "
                              f"invitations will silently fail"}
        return {"set": True, "valid": None,
                "impact": f"could not reach Resend to check ({e}); the key is set"}


def _probe_stripe():
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    hook = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    out = {"secret_key_set": bool(key), "webhook_secret_set": bool(hook),
           "livemode": key.startswith("sk_live_") if key else None}
    if not hook:
        out["impact"] = ("no webhook secret: crittr never receives "
                         "checkout.session.completed, so orders are never marked paid AND "
                         "no vet is ever credited")
    elif not key:
        out["impact"] = "no secret key: checkout cannot be created at all"
    return out


def _probe_llm():
    a = bool(os.environ.get("ANTHROPIC_API_KEY"))
    o = bool(os.environ.get("OPENAI_API_KEY"))
    return {"anthropic_set": a, "openai_set": o,
            "impact": "" if (a or o) else
                      "the chart assistant and AI triage both refuse — no provider"}


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
    db = bool(os.environ.get("DATABASE_URL"))

    blocking = []
    if not db:
        blocking.append("DATABASE_URL")
    if not stripe_["webhook_secret_set"]:
        blocking.append("STRIPE_WEBHOOK_SECRET")
    if resend["set"] is False or resend.get("valid") is False:
        blocking.append("RESEND_API_KEY")
    if not admin["set"]:
        blocking.append("ADMIN_USER/ADMIN_PASS")

    return {
        "ok": not blocking,
        "blocking": blocking,
        "database": {"set": db},
        "email": resend,
        "stripe": stripe_,
        "llm": llm,
        "admin": admin,
        "rev_share_pct": os.environ.get("CRITTR_REV_SHARE_PCT",
                                        os.environ.get("CRITTR_VET_REV_SHARE_PCT", "15")),
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
        row("LLM provider", r["llm"]["anthropic_set"] or r["llm"]["openai_set"],
            r["llm"].get("impact") or "")
        row("Admin credentials", r["admin"]["set"], r["admin"].get("impact", ""))
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
