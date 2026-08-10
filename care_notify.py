"""crittr.ai — aftercare notifications.

A reminder nobody receives is not a reminder. The nightly job already opens follow-ups and
detects lapsed medication courses; this is what actually reaches a person about it.

THREE MESSAGES, and no more than three:
  * a dose is due and has been missed          -> the owner
  * a follow-up is open                        -> the owner
  * an owner has fallen behind on a course     -> the vet

DELIBERATELY QUIET. One digest per owner per day, never one email per dose. A pet owner
with a twice-daily course for a fortnight would otherwise get 28 emails, unsubscribe on
day two, and then miss the one message that mattered. The cap is the feature.

Sends through Resend, exactly like the existing order and abandoned-cart mail, and is a
no-op without RESEND_API_KEY so a missing key degrades to silence rather than a crash.
"""
import os
import logging

log = logging.getLogger("crittr.care_notify")

APP_URL = os.environ.get("APP_URL", "https://crittr.ai").rstrip("/")
FROM = os.environ.get("CRITTR_FROM_EMAIL", "crittr <care@crittr.ai>")


def _send(to_email, subject, html, text):
    """Resend, or silence. Never raises — a notification must not break a nightly job."""
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        log.warning("[care_notify] RESEND_API_KEY not set — skipping send to %s", to_email)
        return False
    if not to_email:
        return False
    try:
        import requests
        r = requests.post("https://api.resend.com/emails",
                          headers={"Authorization": f"Bearer {api_key}",
                                   "Content-Type": "application/json"},
                          json={"from": FROM, "to": [to_email], "subject": subject,
                                "html": html, "text": text},
                          timeout=20)
        if r.status_code >= 300:
            log.warning("[care_notify] send failed %s: %s", r.status_code, r.text[:200])
            return False
        return True
    except Exception as e:
        log.warning("[care_notify] send error: %s", e)
        return False


def _shell(body):
    return (f"<div style=\"font:16px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;"
            f"color:#1C2A1F;max-width:520px\">"
            f"<p style=\"font-weight:800;color:#3E6340;font-size:20px;margin:0 0 16px\">"
            f"crittr</p>{body}"
            f"<p style=\"color:#6E7D70;font-size:13px;margin-top:26px\">"
            f"You're getting this because your veterinarian set up a care plan for your "
            f"pet. <a href=\"{APP_URL}/care\">Manage it here</a>.</p></div>")


def notify_owner_doses(q, q1, owner_user_id, missed):
    """One digest about missed doses. `missed` is a list of {title, due_at}."""
    u = q1("SELECT email, name FROM users WHERE id=%s", (owner_user_id,))
    if not u or not u.get("email"):
        return False
    n = len(missed)
    lines = "".join(f"<li><strong>{m.get('title')}</strong> — due "
                    f"{str(m.get('due_at'))[:16].replace('T', ' ')}</li>"
                    for m in missed[:6])
    subject = (f"{n} missed dose{'s' if n != 1 else ''} for your pet")
    html = _shell(
        f"<p>Hi {u.get('name') or 'there'},</p>"
        f"<p>Your vet's plan has {n} dose{'s' if n != 1 else ''} that hasn't been marked "
        f"as given:</p><ul>{lines}</ul>"
        f"<p><a href=\"{APP_URL}/care\" style=\"background:#527E54;color:#fff;"
        f"padding:11px 18px;border-radius:9px;text-decoration:none;display:inline-block\">"
        f"Mark them off</a></p>"
        f"<p style=\"color:#6E7D70;font-size:14px\">If you've stopped the medication, "
        f"tap Skip so your vet knows — it matters more than you'd think.</p>")
    text = (f"{n} missed dose(s) for your pet.\n\n" +
            "\n".join(f"- {m.get('title')} due {str(m.get('due_at'))[:16]}"
                      for m in missed[:6]) +
            f"\n\nMark them off: {APP_URL}/care\n"
            f"If you've stopped the medication, tap Skip so your vet knows.")
    return _send(u["email"], subject, html, text)


def notify_owner_followup(q, q1, followup):
    """The vet asked to check in."""
    u = q1("SELECT email, name FROM users WHERE id=%s", (followup.get("owner_user_id"),))
    if not u or not u.get("email"):
        return False
    vet = q1("SELECT full_name, clinic_name FROM vets WHERE id=%s",
             (followup.get("vet_id"),)) or {}
    who = vet.get("full_name") or "Your vet"
    url = f"{APP_URL}/care/followup/{followup.get('id')}"
    subject = f"{who} would like to know how your pet is doing"
    html = _shell(
        f"<p>Hi {u.get('name') or 'there'},</p>"
        f"<p><strong>{who}</strong>"
        f"{(' at ' + vet['clinic_name']) if vet.get('clinic_name') else ''} asked to check "
        f"in after your visit.</p>"
        f"<p>A sentence or two is plenty — add a photo if something looks different.</p>"
        f"<p><a href=\"{url}\" style=\"background:#527E54;color:#fff;padding:11px 18px;"
        f"border-radius:9px;text-decoration:none;display:inline-block\">Reply to your vet</a></p>")
    text = (f"{who} asked to check in after your visit.\n\nReply here: {url}")
    return _send(u["email"], subject, html, text)


def notify_vet_lapsed(q, q1, vet_id, rows):
    """The vet hears that an owner has fallen behind, while it still matters."""
    v = q1("SELECT email, full_name FROM vets WHERE id=%s", (vet_id,))
    if not v or not v.get("email"):
        return False
    lines = "".join(f"<li><strong>{r.get('title')}</strong> — "
                    f"{int(r.get('missed') or 0)} doses missed</li>" for r in rows[:8])
    subject = f"{len(rows)} patient{'s' if len(rows) != 1 else ''} behind on medication"
    html = _shell(
        f"<p>Dr {v.get('full_name') or ''},</p>"
        f"<p>These courses you prescribed through crittr have missed doses:</p>"
        f"<ul>{lines}</ul>"
        f"<p style=\"color:#6E7D70;font-size:14px\">Owners mark doses in the app, so this "
        f"reflects what they've recorded rather than what necessarily happened — but it is "
        f"usually worth a call.</p>")
    text = (f"{len(rows)} patient(s) behind on medication:\n" +
            "\n".join(f"- {r.get('title')}: {int(r.get('missed') or 0)} missed"
                      for r in rows[:8]))
    return _send(v["email"], subject, html, text)
