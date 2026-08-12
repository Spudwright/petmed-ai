"""crittr.ai — the practice client book: a clinic brings its own clients across.

WHY THIS IS DIFFERENT FROM WHAT ALREADY EXISTS. vet_aftercare attributes a sale when the
vet wrote that exact product into a care plan. That is narrow on purpose — it credits the
recommendation. But it only ever fires for clients who came to crittr first and for
products the vet happened to list. The clinic's real book of business — the four hundred
owners who already walk through their door — is untouched, and those owners are the ones
buying flea and tick, joint supplements and diets from Chewy every month.

THE MODEL THIS IMPLEMENTS. A clinic sees a client face to face. That in-person examination
is what establishes the VCPR, which is exactly what New Mexico and Texas require and what
crittr can never manufacture remotely. AFTER that visit, the clinic invites the client onto
crittr. The client keeps buying what they were always going to buy, and the margin comes
back to the clinic instead of leaving with Chewy. The clinic is not being paid for a
referral — it is being paid a product margin on its own established clients, which is the
same economics as the shelf behind their front desk, without the shelf.

FIVE INVARIANTS. Each is a way this could hurt a real person if it were wrong.

  1. AN IMPORT IS NOT A MAILING LIST. A named human at the practice must attest, per import,
     that every row is an existing client seen in person and contactable. That attestation
     is stored with their name. Without it nothing is sent to anybody.
  2. ATTRIBUTION BEGINS AT CLAIM, NEVER AT IMPORT. An imported row that the owner has not
     claimed earns the practice nothing. Otherwise a clinic could upload a phone book and
     bill us for strangers.
  3. AN OWNER CAN LEAVE, AND LEAVING IS ONE CLICK. Their account, their pets and their
     order history are theirs; the practice link is not a lock-in.
  4. ONE OWNER, ONE PRACTICE, AT MOST. Claiming a new practice releases the old one, so two
     clinics can never be credited for the same purchase.
  5. RATES ARE FROZEN AT SALE TIME. A clinic reconciles last month against the rate it was
     quoted then, not whatever it is today. Same rule vet_aftercare already follows.

THE ORDER OF PRECEDENCE, when both could apply. Plan attribution outranks practice
attribution: writing the product into a care plan is a clinical act and pays more than
merely owning the relationship. A line item is credited once or not at all — never twice.
"""
import os
import csv
import io
import json
import re
import secrets
import logging
from datetime import datetime, timezone

from flask import request, jsonify, session

import vet_portal as vp
import vet_aftercare as ac

log = logging.getLogger("crittr.practice")

# The practice's share of a sale to one of its claimed clients where no care plan named the
# product. Lower than the plan rate on purpose: the plan rate pays for a clinical
# recommendation, this pays for the relationship. Both are frozen onto the row at sale time.
PRACTICE_REV_SHARE_PCT = int(os.environ.get("CRITTR_PRACTICE_REV_SHARE_PCT", "8"))

SOURCE_PLAN = "plan"
SOURCE_PRACTICE = "practice"

CLIENT_IMPORTED = "imported"    # in the book, never contacted
CLIENT_INVITED = "invited"      # invite sent, not yet accepted
CLIENT_CLAIMED = "claimed"      # the owner accepted — attribution starts here
CLIENT_DECLINED = "declined"    # the owner said no; never contacted again
CLIENT_RELEASED = "released"    # was claimed, then left or moved to another practice

ATTESTATION = (
    "I confirm that every person in this file is an existing client of this practice, that "
    "we have examined their animal in person, and that we have their permission to contact "
    "them about their pet's care."
)


def init_practice_tables(q):
    """Create the practice schema. Safe to call repeatedly."""
    q("""
    CREATE TABLE IF NOT EXISTS practices (
        id              SERIAL PRIMARY KEY,
        vet_id          INTEGER NOT NULL REFERENCES vets(id) ON DELETE CASCADE,
        name            TEXT NOT NULL,
        state           CHAR(2),
        contact_email   TEXT,
        phone           TEXT,
        -- The rate this practice was quoted. Stored per practice so a founding clinic can
        -- be given better terms than the hundredth without a code change.
        rev_share_pct   INTEGER NOT NULL DEFAULT 8,
        status          TEXT NOT NULL DEFAULT 'active',
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""", fetch=False)
    q("""
    CREATE TABLE IF NOT EXISTS practice_imports (
        id              SERIAL PRIMARY KEY,
        practice_id     INTEGER NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
        filename        TEXT,
        rows_seen       INTEGER DEFAULT 0,
        rows_added      INTEGER DEFAULT 0,
        rows_skipped    INTEGER DEFAULT 0,
        -- Invariant 1 lives here. No attested_by, no import, no email to anybody.
        attested_by     TEXT NOT NULL,
        attestation     TEXT NOT NULL,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""", fetch=False)
    q("""
    CREATE TABLE IF NOT EXISTS practice_clients (
        id              SERIAL PRIMARY KEY,
        practice_id     INTEGER NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
        import_id       INTEGER REFERENCES practice_imports(id) ON DELETE SET NULL,
        email           TEXT NOT NULL,
        owner_name      TEXT,
        phone           TEXT,
        pet_name        TEXT,
        species         TEXT,
        -- The date of the in-person examination the clinic is relying on. Recorded because
        -- "when did you last see this animal" is the question a board would ask first.
        last_seen_on    DATE,
        status          TEXT NOT NULL DEFAULT 'imported',
        user_id         INTEGER,
        claim_token     TEXT UNIQUE,
        invited_at      TIMESTAMPTZ,
        claimed_at      TIMESTAMPTZ,
        released_at     TIMESTAMPTZ,
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE (practice_id, email)
    )""", fetch=False)
    # plan_attributions predates this module and only ever held plan-sourced rows. Widen it
    # rather than starting a second earnings table: a clinic wants ONE number, and two
    # tables would eventually disagree about what it is.
    q("ALTER TABLE plan_attributions ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'plan'",
      fetch=False)
    q("ALTER TABLE plan_attributions ADD COLUMN IF NOT EXISTS practice_id INTEGER",
      fetch=False)
    q("""ALTER TABLE plan_attributions
         ADD COLUMN IF NOT EXISTS owner_user_id INTEGER""", fetch=False)
    # Invariant: a given order line is credited at most once, whatever the source. This is
    # the database enforcing "never pay two clinics for one purchase" rather than the
    # application remembering to. It can only fail if history already contains a duplicate,
    # which must be shouted about rather than swallowed: without the index, double payment
    # is possible again, and nobody would notice until a clinic queried its statement.
    try:
        q("""CREATE UNIQUE INDEX IF NOT EXISTS idx_attr_order_line
             ON plan_attributions(order_id, product_id)""", fetch=False)
    except Exception as e:                                  # noqa: BLE001
        dupes = q("""SELECT order_id, product_id, COUNT(*) AS n
                     FROM plan_attributions GROUP BY order_id, product_id
                     HAVING COUNT(*) > 1 LIMIT 10""") or []
        log.error("[practice] could NOT create the one-credit-per-line index (%s). "
                  "Existing duplicates: %s. Double attribution is possible until this is "
                  "resolved.", e, [dict(d) for d in dupes])
    q("""CREATE INDEX IF NOT EXISTS idx_practice_clients_user
         ON practice_clients(user_id) WHERE status='claimed'""", fetch=False)
    q("""CREATE INDEX IF NOT EXISTS idx_practice_clients_practice
         ON practice_clients(practice_id, status)""", fetch=False)


# ── the practice record ──────────────────────────────────────────────────────

def practice_for_vet(q1, vet_id):
    return q1("SELECT * FROM practices WHERE vet_id=%s AND status='active'", (vet_id,))


def create_practice(q, q1, vet, *, name, state=None, contact_email=None, phone=None):
    """One practice per vet account. Re-calling updates rather than duplicating."""
    name = (name or "").strip()
    if not name:
        return None, "the practice needs a name"
    existing = practice_for_vet(q1, vet["id"])
    if existing:
        q("""UPDATE practices SET name=%s, state=%s, contact_email=%s, phone=%s
             WHERE id=%s""",
          (name, (state or "").upper()[:2] or None, contact_email, phone, existing["id"]),
          fetch=False)
        return q1("SELECT * FROM practices WHERE id=%s", (existing["id"],)), ""
    row = q1("""INSERT INTO practices (vet_id, name, state, contact_email, phone,
                                       rev_share_pct)
                VALUES (%s,%s,%s,%s,%s,%s) RETURNING *""",
             (vet["id"], name, (state or "").upper()[:2] or None,
              contact_email or vet.get("email"), phone, PRACTICE_REV_SHARE_PCT))
    vp.audit(q, "practice_created", actor=vet.get("email", ""), vet_id=vet["id"],
             detail={"name": name, "state": state})
    return row, ""


# ── importing the client book ────────────────────────────────────────────────

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")

# Clinics export from Cornerstone, AVImark, ezyVet and half a dozen others, and every one
# names its columns differently. Matching on aliases means the vet uploads whatever their
# PIMS gave them instead of reformatting a spreadsheet they do not want to reformat.
_ALIASES = {
    "email": ("email", "e-mail", "client email", "owner email", "email address",
              "clientemail", "primary email"),
    "owner_name": ("owner", "owner name", "client", "client name", "last name, first name",
                   "name", "clientname", "owner_name"),
    "phone": ("phone", "mobile", "cell", "telephone", "phone number", "primary phone"),
    "pet_name": ("pet", "pet name", "patient", "patient name", "animal", "petname"),
    "species": ("species", "type", "animal type"),
    "last_seen_on": ("last seen", "last visit", "last exam", "last_seen_on",
                     "last visit date", "last appointment"),
}


def _norm(s):
    return re.sub(r"[^a-z ]", " ", (s or "").strip().lower()).strip()


def _map_columns(fieldnames):
    """Map a PIMS export's headers onto our fields. Unrecognised columns are ignored."""
    out = {}
    for raw in (fieldnames or []):
        n = _norm(raw)
        for field, names in _ALIASES.items():
            if field in out:
                continue
            if n in names or n.replace(" ", "") in [x.replace(" ", "") for x in names]:
                out[field] = raw
                break
    return out


def parse_roster(text):
    """CSV text -> (rows, problems). Never raises on a malformed file.

    A row without a usable email is reported rather than dropped silently: a clinic that
    uploads 400 rows and sees "312 imported" needs to know where the other 88 went, or they
    will assume we lost them.
    """
    rows, problems = [], []
    try:
        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except Exception:
            dialect = csv.excel
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        cols = _map_columns(reader.fieldnames)
        if "email" not in cols:
            return [], [f"no email column found — headers were: "
                        f"{', '.join(reader.fieldnames or []) or '(none)'}"]
        for i, raw in enumerate(reader, start=2):
            email = (raw.get(cols["email"]) or "").strip().lower()
            if not email:
                problems.append(f"row {i}: no email")
                continue
            if not _EMAIL_RE.match(email):
                problems.append(f"row {i}: '{email[:40]}' is not a valid email")
                continue
            rec = {"email": email}
            for field, col in cols.items():
                if field == "email":
                    continue
                val = (raw.get(col) or "").strip()
                if val:
                    rec[field] = val[:200]
            rows.append(rec)
    except Exception as e:                                  # noqa: BLE001
        problems.append(f"could not read the file: {e}")
    return rows, problems


def _parse_date(v):
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y", "%d %b %Y", "%b %d %Y"):
        try:
            return datetime.strptime(str(v).strip(), fmt).date()
        except Exception:
            continue
    return None


def import_roster(q, q1, vet, practice_id, rows, *, attested_by, filename=""):
    """Write a parsed roster into the client book.

    Invariant 1: attested_by is required and stored. Rows land as 'imported' — nothing is
    sent to anyone here. Inviting is a separate, deliberate second action.
    """
    attested_by = (attested_by or "").strip()
    if not attested_by:
        return None, ("name the person at the practice making this attestation — an import "
                      "is a statement about real people and has to be signed")
    if not rows:
        return None, "nothing to import"
    imp = q1("""INSERT INTO practice_imports
                (practice_id, filename, rows_seen, attested_by, attestation)
                VALUES (%s,%s,%s,%s,%s) RETURNING id""",
             (practice_id, filename[:200], len(rows), attested_by, ATTESTATION))
    import_id = imp["id"] if imp else None
    added = skipped = 0
    for r in rows:
        seen = _parse_date(r.get("last_seen_on")) if r.get("last_seen_on") else None
        res = q1("""INSERT INTO practice_clients
                    (practice_id, import_id, email, owner_name, phone, pet_name, species,
                     last_seen_on, status)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'imported')
                    ON CONFLICT (practice_id, email) DO NOTHING
                    RETURNING id""",
                 (practice_id, import_id, r["email"], r.get("owner_name"), r.get("phone"),
                  r.get("pet_name"), r.get("species"), seen))
        if res:
            added += 1
        else:
            skipped += 1
    q("UPDATE practice_imports SET rows_added=%s, rows_skipped=%s WHERE id=%s",
      (added, skipped, import_id), fetch=False)
    vp.audit(q, "practice_roster_imported", actor=attested_by, vet_id=vet["id"],
             detail={"practice_id": practice_id, "import_id": import_id,
                     "added": added, "skipped_already_present": skipped,
                     "attestation": ATTESTATION})
    return {"import_id": import_id, "added": added, "skipped": skipped,
            "attested_by": attested_by}, ""


# ── invitation and claim ─────────────────────────────────────────────────────

def _app_url():
    return os.environ.get("APP_URL", "https://crittr.ai").rstrip("/")


def invite_clients(q, q1, vet, practice_id, *, client_ids=None, limit=500):
    """Send the invite. Only to rows in this practice, only 'imported' ones.

    The email comes FROM the clinic's name and replies go to the clinic, because the owner's
    relationship is with their veterinarian and an invite that reads like a crittr
    advertisement is both less honest and less likely to be opened.
    """
    practice = q1("SELECT * FROM practices WHERE id=%s", (practice_id,))
    if not practice:
        return None, "no such practice"
    if client_ids:
        rows = q("""SELECT * FROM practice_clients
                    WHERE practice_id=%s AND id = ANY(%s) AND status='imported'
                    LIMIT %s""",
                 (practice_id, list(client_ids), int(limit))) or []
    else:
        rows = q("""SELECT * FROM practice_clients
                    WHERE practice_id=%s AND status='imported'
                    ORDER BY id LIMIT %s""", (practice_id, int(limit))) or []
    sent, failed = 0, 0
    for c in rows:
        token = secrets.token_urlsafe(24)
        q("""UPDATE practice_clients SET claim_token=%s, status='invited', invited_at=NOW()
             WHERE id=%s""", (token, c["id"]), fetch=False)
        if _send_invite(practice, c, token):
            sent += 1
        else:
            failed += 1
    vp.audit(q, "practice_clients_invited", actor=vet.get("email", ""), vet_id=vet["id"],
             detail={"practice_id": practice_id, "sent": sent, "failed": failed})
    return {"sent": sent, "failed": failed, "selected": len(rows)}, ""


def _send_invite(practice, client, token):
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        log.warning("[practice] RESEND_API_KEY not set — invite not sent to %s",
                    client.get("email"))
        return False
    url = f"{_app_url()}/vet/claim/{token}"
    clinic = practice.get("name") or "your veterinary practice"
    pet = client.get("pet_name")
    who = f" for {pet}" if pet else ""
    greeting = f"Hello {client['owner_name'].split()[0]}," if client.get("owner_name") \
        else "Hello,"
    html = f"""
      <div style="font:16px/1.55 -apple-system,Segoe UI,Roboto,sans-serif;color:#1C2A1F;
                  max-width:520px">
        <p>{greeting}</p>
        <p><strong>{clinic}</strong> has set up online care{who} through crittr — the food,
           supplements and refills we recommend, delivered to you, with reminders so doses
           don't get missed.</p>
        <p>It stays with us: when you order through this link, your practice still looks
           after the care and still benefits, instead of the order going to a warehouse that
           has never met your animal.</p>
        <p style="margin:26px 0">
          <a href="{url}" style="background:#527E54;color:#fff;text-decoration:none;
             padding:13px 22px;border-radius:9px;font-weight:700;display:inline-block">
            Connect with {clinic}</a></p>
        <p style="color:#6E7D70;font-size:14px">If you'd rather not, ignore this email —
           nothing changes and we won't send another. You can disconnect at any time.</p>
      </div>"""
    text = (f"{greeting}\n\n{clinic} has set up online care{who} through crittr.\n\n"
            f"Connect here: {url}\n\n"
            f"If you'd rather not, ignore this email — we won't send another.")
    try:
        import resend
        resend.api_key = api_key
        resend.Emails.send({
            "from": os.environ.get("FROM_EMAIL", "crittr <hello@crittr.ai>"),
            "to": [client["email"]],
            "subject": f"{clinic}: your pet's care, delivered",
            "html": html,
            "text": text,
            "reply_to": practice.get("contact_email")
                        or os.environ.get("REPLY_TO_EMAIL", "hello@crittr.ai"),
        })
        return True
    except Exception as e:                                  # noqa: BLE001
        log.error("[practice] invite send failed for %s: %s", client.get("email"), e)
        return False


def client_for_token(q1, token):
    if not token:
        return None
    return q1("""SELECT c.*, p.name AS practice_name, p.id AS pid
                 FROM practice_clients c JOIN practices p ON p.id=c.practice_id
                 WHERE c.claim_token=%s""", (token,))


def claim(q, q1, token, user_id):
    """The owner accepts. Invariant 4: this releases any previous practice link."""
    c = client_for_token(q1, token)
    if not c:
        return None, "that invitation link is not valid"
    if c["status"] == CLIENT_DECLINED:
        return None, "that invitation was declined"
    if c["status"] == CLIENT_CLAIMED and c.get("user_id") == user_id:
        return c, ""
    q("""UPDATE practice_clients SET status='released', released_at=NOW()
         WHERE user_id=%s AND status='claimed' AND id<>%s""",
      (user_id, c["id"]), fetch=False)
    q("""UPDATE practice_clients
         SET status='claimed', user_id=%s, claimed_at=NOW(), claim_token=NULL
         WHERE id=%s""", (user_id, c["id"]), fetch=False)
    vp.audit(q, "practice_client_claimed", actor=str(user_id),
             detail={"practice_id": c["practice_id"], "client_id": c["id"]})
    return q1("SELECT * FROM practice_clients WHERE id=%s", (c["id"],)), ""


def decline(q, q1, token):
    """Invariant 3, the honest half: no is recorded and never asked again."""
    c = client_for_token(q1, token)
    if not c:
        return False, "that invitation link is not valid"
    q("""UPDATE practice_clients SET status='declined', claim_token=NULL WHERE id=%s""",
      (c["id"],), fetch=False)
    vp.audit(q, "practice_client_declined", actor="owner",
             detail={"practice_id": c["practice_id"], "client_id": c["id"]})
    return True, ""


def release(q, q1, user_id):
    """The owner leaves. One click, no negotiation, keeps everything of theirs."""
    row = practice_client_for_user(q1, user_id)
    if not row:
        return False, "you are not connected to a practice"
    q("""UPDATE practice_clients SET status='released', released_at=NOW() WHERE id=%s""",
      (row["id"],), fetch=False)
    vp.audit(q, "practice_client_released", actor=str(user_id),
             detail={"practice_id": row["practice_id"], "client_id": row["id"]})
    return True, ""


def practice_client_for_user(q1, user_id):
    """The live practice link for a buyer, or None. Only 'claimed' counts (invariant 2)."""
    if not user_id:
        return None
    return q1("""SELECT c.*, p.rev_share_pct, p.vet_id, p.name AS practice_name
                 FROM practice_clients c JOIN practices p ON p.id=c.practice_id
                 WHERE c.user_id=%s AND c.status='claimed' AND p.status='active'
                 ORDER BY c.claimed_at DESC LIMIT 1""", (user_id,))


# ── attribution: the part that actually pays the clinic ──────────────────────

def attribute_order(q, q1, *, order_id, items, owner_user_id):
    """Credit every line of a paid order. THE entry point from the Stripe webhook.

    Per line, in order of precedence:
      1. a care plan of this owner's names that product  -> plan rate, credited to that vet
      2. this owner is a claimed client of a practice    -> practice rate, to that practice
      3. neither                                         -> nobody is credited, which is the
                                                            normal case for a cold shop sale

    Returns a summary dict. Deliberately swallows nothing: a failure here must not roll back
    a payment the customer already made, so the CALLER wraps it — but a silent zero would
    hide a broken revenue share for months, so it logs loudly.
    """
    out = {"order_id": order_id, "plan_cents": 0, "practice_cents": 0, "lines": 0}
    # Stripe retries webhooks — the same checkout.session.completed can arrive several
    # times. Crediting an order twice would pay a clinic twice for one purchase, so the
    # first thing this does is ask whether it has already run for this order.
    already = q1("SELECT 1 AS x FROM plan_attributions WHERE order_id=%s LIMIT 1",
                 (order_id,))
    if already:
        out["skipped"] = "already credited"
        return out
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except Exception:
            items = []
    link = practice_client_for_user(q1, owner_user_id)
    for it in (items or []):
        pid = it.get("product_id")
        if not pid:
            continue
        amount = int(it.get("price_cents") or 0) * int(it.get("quantity") or 1)
        if amount <= 0:
            continue
        out["lines"] += 1
        share = ac.attribute_sale(q, q1, order_id=order_id, product_id=pid,
                                  amount_cents=amount, owner_user_id=owner_user_id)
        if share:
            q("""UPDATE plan_attributions SET source='plan', owner_user_id=%s
                 WHERE order_id=%s AND product_id=%s""",
              (owner_user_id, order_id, pid), fetch=False)
            out["plan_cents"] += share
            continue
        if not link:
            continue
        pct = int(link.get("rev_share_pct") or PRACTICE_REV_SHARE_PCT)
        cents = int(round(amount * pct / 100.0))
        q("""INSERT INTO plan_attributions
             (vet_id, practice_id, owner_user_id, order_id, product_id, amount_cents,
              share_pct, share_cents, source)
             VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'practice')
             ON CONFLICT (order_id, product_id) DO NOTHING""",
          (link["vet_id"], link["practice_id"], owner_user_id, order_id, pid, amount,
           pct, cents), fetch=False)
        out["practice_cents"] += cents
    if out["plan_cents"] or out["practice_cents"]:
        log.info("[practice] order %s credited: plan %s¢, practice %s¢",
                 order_id, out["plan_cents"], out["practice_cents"])
    return out


def practice_earnings(q, q1, practice_id, days=30):
    """What the clinic has earned, split by why. The number the partner logs in to see."""
    rows = q("""SELECT source, COALESCE(SUM(share_cents),0) AS cents, COUNT(*) AS n
                FROM plan_attributions
                WHERE (practice_id=%s OR vet_id=(SELECT vet_id FROM practices WHERE id=%s))
                  AND created_at > NOW() - (%s || ' days')::interval
                GROUP BY source""",
             (practice_id, practice_id, str(int(days)))) or []
    by = {r["source"] or "plan": {"cents": int(r["cents"]), "orders": int(r["n"])}
          for r in rows}
    total = sum(v["cents"] for v in by.values())
    return {"days": days, "total_cents": total, "by_source": by}


def practice_book(q, q1, practice_id):
    """The client book with its funnel: imported -> invited -> claimed."""
    rows = q("""SELECT status, COUNT(*) AS n FROM practice_clients
                WHERE practice_id=%s GROUP BY status""", (practice_id,)) or []
    counts = {r["status"]: int(r["n"]) for r in rows}
    total = sum(counts.values())
    claimed = counts.get(CLIENT_CLAIMED, 0)
    invited = counts.get(CLIENT_INVITED, 0) + claimed + counts.get(CLIENT_DECLINED, 0)
    return {"total": total, "counts": counts,
            "claim_rate_pct": round(100.0 * claimed / invited, 1) if invited else None}


# ── HTTP surface ─────────────────────────────────────────────────────────────

def register_practice_routes(app, q, q1):
    vet_only = vp.require_vet(q1)

    @app.route("/api/vet/practice", methods=["GET", "POST"])
    @vet_only
    def api_practice(vet):
        if request.method == "POST":
            d = request.get_json(silent=True) or {}
            row, why = create_practice(q, q1, vet, name=d.get("name"),
                                       state=d.get("state"),
                                       contact_email=d.get("contact_email"),
                                       phone=d.get("phone"))
            if not row:
                return jsonify({"error": why}), 400
            return jsonify({"ok": True, "practice": _ser(row)})
        row = practice_for_vet(q1, vet["id"])
        if not row:
            return jsonify({"practice": None,
                            "next": "create your practice to start bringing clients across"})
        return jsonify({"practice": _ser(row), "book": practice_book(q, q1, row["id"]),
                        "earnings": practice_earnings(q, q1, row["id"])})

    @app.route("/api/vet/practice/import", methods=["POST"])
    @vet_only
    def api_practice_import(vet):
        """Upload the client book. Accepts a file upload or raw CSV text."""
        practice = practice_for_vet(q1, vet["id"])
        if not practice:
            return jsonify({"error": "create your practice first"}), 400
        attested_by = (request.form.get("attested_by")
                       or (request.get_json(silent=True) or {}).get("attested_by") or "")
        filename = ""
        text = ""
        if "file" in request.files:
            f = request.files["file"]
            filename = f.filename or ""
            text = f.read().decode("utf-8", errors="replace")
        else:
            d = request.get_json(silent=True) or {}
            text = d.get("csv") or request.form.get("csv") or ""
        if not text.strip():
            return jsonify({"error": "no CSV content received"}), 400
        rows, problems = parse_roster(text)
        if not rows:
            return jsonify({"error": "no usable rows", "problems": problems[:20]}), 400
        res, why = import_roster(q, q1, vet, practice["id"], rows,
                                 attested_by=attested_by, filename=filename)
        if not res:
            return jsonify({"error": why}), 400
        res["problems"] = problems[:20]
        res["problem_count"] = len(problems)
        res["next"] = ("nothing has been sent yet — review the book, then send invitations "
                       "when you're ready")
        return jsonify({"ok": True, **res})

    @app.route("/api/vet/practice/clients", methods=["GET"])
    @vet_only
    def api_practice_clients(vet):
        practice = practice_for_vet(q1, vet["id"])
        if not practice:
            return jsonify({"clients": [], "book": None})
        status = (request.args.get("status") or "").strip()
        if status:
            rows = q("""SELECT * FROM practice_clients WHERE practice_id=%s AND status=%s
                        ORDER BY id LIMIT 500""", (practice["id"], status)) or []
        else:
            rows = q("""SELECT * FROM practice_clients WHERE practice_id=%s
                        ORDER BY id LIMIT 500""", (practice["id"],)) or []
        return jsonify({"clients": [_ser(r) for r in rows],
                        "book": practice_book(q, q1, practice["id"])})

    @app.route("/api/vet/practice/invite", methods=["POST"])
    @vet_only
    def api_practice_invite(vet):
        practice = practice_for_vet(q1, vet["id"])
        if not practice:
            return jsonify({"error": "create your practice first"}), 400
        d = request.get_json(silent=True) or {}
        res, why = invite_clients(q, q1, vet, practice["id"],
                                  client_ids=d.get("client_ids"),
                                  limit=int(d.get("limit") or 500))
        if not res:
            return jsonify({"error": why}), 400
        return jsonify({"ok": True, **res})

    @app.route("/api/vet/practice/earnings", methods=["GET"])
    @vet_only
    def api_practice_earnings(vet):
        practice = practice_for_vet(q1, vet["id"])
        if not practice:
            return jsonify({"error": "create your practice first"}), 400
        days = int(request.args.get("days") or 30)
        return jsonify({"practice": practice["name"],
                        "rev_share_pct": practice["rev_share_pct"],
                        **practice_earnings(q, q1, practice["id"], days)})

    # ── the owner's side ─────────────────────────────────────────────────────

    @app.route("/api/practice/claim", methods=["POST"])
    def api_claim():
        uid = session.get("user_id")
        if not uid:
            return jsonify({"error": "sign in or create an account first",
                            "needs_auth": True}), 401
        d = request.get_json(silent=True) or {}
        row, why = claim(q, q1, (d.get("token") or "").strip(), uid)
        if not row:
            return jsonify({"error": why}), 400
        return jsonify({"ok": True, "connected": True})

    @app.route("/api/practice/decline", methods=["POST"])
    def api_decline():
        d = request.get_json(silent=True) or {}
        ok, why = decline(q, q1, (d.get("token") or "").strip())
        return (jsonify({"ok": True}) if ok else (jsonify({"error": why}), 400))

    @app.route("/api/practice/me", methods=["GET"])
    def api_my_practice():
        row = practice_client_for_user(q1, session.get("user_id"))
        return jsonify({"practice": row["practice_name"] if row else None,
                        "connected": bool(row)})

    @app.route("/api/practice/release", methods=["POST"])
    def api_release():
        uid = session.get("user_id")
        if not uid:
            return jsonify({"error": "sign in first"}), 401
        ok, why = release(q, q1, uid)
        return (jsonify({"ok": True}) if ok else (jsonify({"error": why}), 400))


def _ser(obj):
    if obj is None:
        return None
    out = {}
    for k, v in dict(obj).items():
        if k == "claim_token":          # never leave the server in a list response
            continue
        out[k] = v if isinstance(v, (int, float, bool, type(None))) else str(v)
    return out
