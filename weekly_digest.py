"""crittr.ai — Monday weekly digest email (Phase 7.3).

Sends each active user one email summarizing:
  * Each pet's last summary blob / status
  * One curated product pick per pet
  * One seasonal health tip

Ships as a cron job: run every Monday ~7am local. Idempotent — if the
same digest has been sent in the last 6 days (tracked via email_sends),
skip.

Usage
-----
    python weekly_digest.py                 # send digests
    python weekly_digest.py --dry-run       # render + print, don't send
    python weekly_digest.py --user 42       # only user 42 (useful for QA)

Public API
----------
    build_digest(user_row, pets, tip) -> (subject, html, text)
    send_digests(q, dry_run=False, only_user_id=None) -> dict

Dependencies
------------
  * emails.py — send_email(to, subject, body, html?) helper
  * products.py — get_top_picks helper (we pull one pick per pet)
"""
import os
import sys
import json
import argparse
import logging
from datetime import date

log = logging.getLogger("crittr.digest")


# ---------------------------------------------------------------
# Seasonal tips — indexed by ISO week // 4 (monthly-ish rotation)
# ---------------------------------------------------------------
_TIPS = [
    # (title, body, seasonal_hint_month_range)
    ("Allergy season is ramping up",
     "If your dog's feet are red or they're licking paws constantly, it's "
     "probably environmental. An oatmeal rinse after walks can help.",
     (3, 5)),
    ("Heat stroke is the summer villain",
     "Brachycephalic breeds (bulldogs, pugs, Frenchies) get overwhelmed fast. "
     "Keep walks early and short; watch for heavy panting that doesn't settle.",
     (6, 8)),
    ("Fireworks week — plan ahead",
     "If you've got a nervous dog, start anti-anxiety wraps a few days early. "
     "Vets book up fast the week of the 4th.",
     (6, 7)),
    ("Fall is tick season's encore",
     "Keep preventatives going through October. Do a weekly tick check on "
     "anyone who walks in tall grass.",
     (9, 11)),
    ("Holiday food hazards",
     "Chocolate, grapes, onions, xylitol, and fatty table scraps all peak "
     "around holidays. Brief the family before they start sneaking bites.",
     (11, 12)),
    ("Winter paw care",
     "Rock salt on sidewalks dries out paws and burns if licked. Wipe paws "
     "after walks; a thin layer of paw balm helps.",
     (12, 2)),
]


def _current_tip():
    m = date.today().month
    for title, body, rng in _TIPS:
        lo, hi = rng
        if lo <= hi:
            if lo <= m <= hi:
                return (title, body)
        else:  # wraps year-end (12..2)
            if m >= lo or m <= hi:
                return (title, body)
    t = _TIPS[0]
    return (t[0], t[1])


# ---------------------------------------------------------------
# Digest render
# ---------------------------------------------------------------
def _pet_block(pet, pick=None):
    """Returns (text_block, html_block) for one pet."""
    name = pet.get("name") or "your pet"
    summary = (pet.get("summary") or "").strip()
    last_seen = pet.get("last_msg_at")
    status_line = (
        f"Last checked in: {last_seen.strftime('%B %d')}"
        if last_seen else "No recent check-ins"
    )
    text_parts = [f"{name} — {status_line}"]
    html_parts = [
        f"<h3 style='margin:0 0 6px 0;font-family:Georgia,serif;color:#2D4A30;'>{name}</h3>",
        f"<div style='color:#6E7D70;font-size:13px;margin-bottom:10px;'>{status_line}</div>",
    ]
    if summary:
        # Soft cap for email
        if len(summary) > 600:
            summary = summary[:590].rsplit(" ", 1)[0] + "…"
        text_parts.append(summary)
        html_parts.append(
            f"<p style='margin:0 0 14px 0;font-size:15px;line-height:1.55;'>{summary}</p>"
        )
    if pick:
        reason = (pick.get("reason") or "").strip()
        name_p = pick.get("name") or pick.get("slug") or "Recommended product"
        url_p = pick.get("url") or "#"
        text_parts.append(f"Pick this week: {name_p} — {reason}")
        html_parts.append(
            "<div style='background:#F2F7F1;border-radius:10px;"
            "padding:12px 14px;margin-bottom:16px;'>"
            "<div style='font-size:12px;color:#527E54;letter-spacing:.06em;"
            "text-transform:uppercase;margin-bottom:4px;'>Pick for "
            f"{name}</div>"
            f"<a href='{url_p}' style='color:#2D4A30;font-weight:600;"
            f"text-decoration:none;font-size:15px;'>{name_p}</a>"
            + (f"<div style='font-size:13px;color:#374C3C;margin-top:4px;'>"
               f"{reason}</div>" if reason else "")
            + "</div>"
        )
    return "\n".join(text_parts), "\n".join(html_parts)


def build_digest(user_row, pets_with_picks, tip):
    """Compose subject, HTML body, and plain-text body for one user.

    Args:
        user_row: {"id", "email", "name"?}
        pets_with_picks: [{"pet": pet_row_dict, "pick": pick_dict_or_None}]
        tip: (title, body)
    Returns: (subject, html, text)
    """
    first = (user_row.get("name") or "").split(" ")[0] or "there"
    pet_count = len(pets_with_picks)
    subject = (
        f"Monday check-in for {pets_with_picks[0]['pet']['name']}" if pet_count == 1
        else f"Monday check-in for your {pet_count} pets"
    )

    tip_title, tip_body = tip

    # HTML
    blocks = [_pet_block(pp["pet"], pp.get("pick")) for pp in pets_with_picks]
    html_pets = "".join(html for _, html in blocks)
    text_pets = "\n\n".join(txt for txt, _ in blocks)

    html = f"""<!doctype html>
<html><body style="margin:0;padding:0;background:#FDFBF5;
font-family:Helvetica,Arial,sans-serif;color:#1C2A1F;">
<div style="max-width:560px;margin:0 auto;padding:28px 20px;">
  <div style="font-family:Georgia,serif;font-size:22px;color:#2D4A30;
     margin-bottom:4px;">Good morning, {first}</div>
  <div style="color:#6E7D70;font-size:14px;margin-bottom:24px;">
    Your Monday crittr check-in.</div>

  {html_pets}

  <div style="border-top:1px dashed #DFE5DB;padding-top:18px;margin-top:10px;">
    <div style="font-family:Georgia,serif;font-size:17px;color:#2D4A30;
       margin-bottom:6px;">{tip_title}</div>
    <div style="font-size:14px;line-height:1.55;color:#374C3C;">{tip_body}</div>
  </div>

  <div style="color:#6E7D70;font-size:12px;margin-top:28px;
     text-align:center;">
    crittr.ai · <a href="https://crittr.ai/settings"
       style="color:#527E54;">manage digest</a>
  </div>
</div>
</body></html>
"""

    text = (
        f"Good morning, {first}.\n\n"
        f"Your Monday crittr check-in.\n\n"
        f"{text_pets}\n\n"
        f"--- {tip_title} ---\n{tip_body}\n\n"
        f"crittr.ai · https://crittr.ai/settings to manage digest"
    )
    return subject, html, text


# ---------------------------------------------------------------
# Data access
# ---------------------------------------------------------------
def _fetch_users(q, only_user_id=None):
    try:
        if only_user_id:
            rows = q(
                "SELECT id, email, name FROM users "
                "WHERE id = %s AND digest_opt_in IS NOT FALSE;",
                (only_user_id,),
            )
        else:
            rows = q(
                "SELECT id, email, name FROM users "
                "WHERE digest_opt_in IS NOT FALSE "
                "  AND last_active_at > NOW() - INTERVAL '90 days';"
            )
        return rows or []
    except Exception as e:
        log.warning("[digest] users query failed: %s", e)
        return []


def _fetch_pets_for_user(q, user_id):
    try:
        rows = q(
            """
            SELECT p.id, p.name, p.species, p.breed, p.birthdate,
                   (SELECT summary FROM pet_chat_summaries s
                      WHERE s.pet_id = p.id
                      ORDER BY s.created_at DESC LIMIT 1) AS summary,
                   (SELECT MAX(created_at) FROM pet_chat_messages m
                      WHERE m.pet_id = p.id) AS last_msg_at
            FROM pets p
            WHERE p.user_id = %s AND p.is_active = TRUE;
            """,
            (user_id,),
        )
        return rows or []
    except Exception as e:
        log.warning("[digest] pets query failed: %s", e)
        return []


def _pick_for_pet(pet):
    try:
        from products import get_top_picks
    except Exception:
        return None
    try:
        picks = get_top_picks(pet, question="weekly digest", n=1) or []
        return picks[0] if picks else None
    except Exception as e:
        log.warning("[digest] pick lookup failed: %s", e)
        return None


def _already_sent_this_week(q, user_id):
    try:
        rows = q(
            "SELECT 1 FROM email_sends "
            "WHERE user_id = %s AND kind = 'weekly_digest' "
            "  AND sent_at > NOW() - INTERVAL '6 days' LIMIT 1;",
            (user_id,),
        )
        return bool(rows)
    except Exception:
        # If the table isn't there yet, assume not sent.
        return False


def _record_send(q, user_id):
    try:
        q(
            """
            CREATE TABLE IF NOT EXISTS email_sends (
              id BIGSERIAL PRIMARY KEY,
              user_id BIGINT NOT NULL,
              kind TEXT NOT NULL,
              sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS ix_email_sends_user_kind
              ON email_sends(user_id, kind, sent_at DESC);
            """,
            fetch=False,
        )
        q("INSERT INTO email_sends (user_id, kind) VALUES (%s, 'weekly_digest');",
          (user_id,), fetch=False)
    except Exception as e:
        log.warning("[digest] record_send failed: %s", e)


# ---------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------
def send_digests(q, dry_run=False, only_user_id=None):
    try:
        from emails import send_email
    except Exception as e:
        log.warning("[digest] emails.py unavailable: %s", e)
        send_email = None

    tip = _current_tip()
    users = _fetch_users(q, only_user_id=only_user_id)
    out = {"candidates": len(users), "sent": 0, "skipped": 0, "errors": 0}

    for u in users:
        uid = u["id"]
        if not dry_run and _already_sent_this_week(q, uid):
            out["skipped"] += 1
            continue
        pets = _fetch_pets_for_user(q, uid)
        if not pets:
            out["skipped"] += 1
            continue
        pets_with_picks = [{"pet": p, "pick": _pick_for_pet(p)} for p in pets]
        subject, html, text = build_digest(u, pets_with_picks, tip)

        if dry_run or send_email is None:
            print(f"---- digest for {u.get('email')} ----")
            print("Subject:", subject)
            print(text)
            print()
            continue
        try:
            send_email(to=u["email"], subject=subject, body=text, html=html)
            _record_send(q, uid)
            out["sent"] += 1
        except Exception as e:
            log.warning("[digest] send to %s failed: %s", u.get("email"), e)
            out["errors"] += 1

    return out


def _load_q_from_app():
    try:
        from app import q  # type: ignore
        return q
    except Exception as e:
        log.warning("[digest] q import failed: %s", e)
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--user", type=int)
    args = ap.parse_args()
    q = _load_q_from_app()
    if q is None and not args.dry_run:
        print(json.dumps({"error": "no db handle"}))
        return 2
    out = send_digests(q, dry_run=args.dry_run, only_user_id=args.user)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
