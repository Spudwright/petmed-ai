"""crittr.ai — the veterinarian side.

WHAT THIS EXISTS FOR. Until now crittr referred owners OUT: find-a-vet by geolocation and
affiliate links to Vetster/AirVet/Chewy. A partner vet had nothing to log into and never
touched the product. This is the vet-facing half — accounts, licence verification, a case
queue, VCPR records and an audit trail — so a real veterinarian can sign up, be verified,
and review cases in a state we are permitted to operate in.

DESIGNED FOR NEW MEXICO FIRST, THEN STATE BY STATE. Nothing here knows anything about New
Mexico specifically. Every gate reads vet_compliance, which is default-deny per state, so
launching NM means answering NM's questions and activating it; launching the next state
means doing the same again. No code changes either time.

FOUR INVARIANTS, each of which is a way this could hurt someone if it were wrong:

  1. A vet only ever sees cases in a state they are LICENSED in and that is ACTIVE.
  2. An expired or unverified licence sees nothing at all.
  3. Every clinical action is written to an append-only audit log with the actor, because
     this is medical-adjacent and "who decided that, and when" must always be answerable.
  4. The AI's verdict is never presented as the vet's. A vet confirms or overrides, and
     which of those happened is recorded.
"""
import os
import json
import secrets
import logging
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import request, jsonify, session

import vet_compliance as vc

log = logging.getLogger("crittr.vet")

VET_STATUS_PENDING = "pending"      # applied, not yet checked
VET_STATUS_VERIFIED = "verified"    # licence checked by a human
VET_STATUS_SUSPENDED = "suspended"

CASE_QUEUED = "queued"
CASE_CLAIMED = "claimed"
CASE_REVIEWED = "reviewed"
CASE_CLOSED = "closed"


def init_vet_tables(q):
    """Create the vet-side schema. Safe to call repeatedly."""
    vc.init_compliance_tables(q)
    q("""
    CREATE TABLE IF NOT EXISTS vets (
        id              SERIAL PRIMARY KEY,
        user_id         INTEGER REFERENCES users(id) ON DELETE CASCADE,
        full_name       TEXT NOT NULL,
        clinic_name     TEXT,
        email           TEXT NOT NULL,
        phone           TEXT,
        status          TEXT NOT NULL DEFAULT 'pending',
        -- Verification is a HUMAN act, recorded with a name. We do not scrape a licence
        -- board and call that verified.
        verified_by     TEXT,
        verified_at     TIMESTAMPTZ,
        notes           TEXT,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""", fetch=False)
    q("""
    CREATE TABLE IF NOT EXISTS vet_licenses (
        id              SERIAL PRIMARY KEY,
        vet_id          INTEGER NOT NULL REFERENCES vets(id) ON DELETE CASCADE,
        state           CHAR(2) NOT NULL,
        license_number  TEXT NOT NULL,
        -- Expiry is enforced, not decorative: an expired licence stops case access the day
        -- it lapses, without anyone remembering to switch it off.
        expires_on      DATE,
        verified_by     TEXT,
        verified_at     TIMESTAMPTZ,
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE (vet_id, state, license_number)
    )""", fetch=False)
    q("""
    CREATE TABLE IF NOT EXISTS vet_cases (
        id              SERIAL PRIMARY KEY,
        state           CHAR(2) NOT NULL,
        owner_user_id   INTEGER,
        pet_id          INTEGER,
        source          TEXT,            -- 'triage' | 'manual'
        source_ref      TEXT,            -- anon_chats id / chat ref
        ai_verdict      TEXT,            -- ER NOW | VET TOMORROW | SAFE AT HOME
        ai_reasoning    TEXT,
        owner_message   TEXT,
        photo_url       TEXT,
        status          TEXT NOT NULL DEFAULT 'queued',
        assigned_vet_id INTEGER REFERENCES vets(id) ON DELETE SET NULL,
        vet_verdict     TEXT,
        vet_notes       TEXT,
        -- Was the AI right? Recorded explicitly so the override rate is measurable rather
        -- than anecdotal — it is the number that tells us whether the triage is safe.
        agreed_with_ai  BOOLEAN,
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        claimed_at      TIMESTAMPTZ,
        reviewed_at     TIMESTAMPTZ
    )""", fetch=False)
    q("""
    CREATE TABLE IF NOT EXISTS vcpr_records (
        id              SERIAL PRIMARY KEY,
        vet_id          INTEGER NOT NULL REFERENCES vets(id) ON DELETE CASCADE,
        owner_user_id   INTEGER NOT NULL,
        pet_id          INTEGER,
        state           CHAR(2) NOT NULL,
        -- 'in_person' | 'telemedicine'. Which one is legal depends entirely on the state,
        -- which is why establish_vcpr() asks vet_compliance before writing this row.
        method          TEXT NOT NULL,
        established_at  TIMESTAMPTZ DEFAULT NOW(),
        expires_at      TIMESTAMPTZ,
        established_by  TEXT,
        revoked_at      TIMESTAMPTZ,
        UNIQUE (vet_id, owner_user_id, pet_id)
    )""", fetch=False)
    q("""
    CREATE TABLE IF NOT EXISTS vet_audit (
        id          SERIAL PRIMARY KEY,
        at          TIMESTAMPTZ DEFAULT NOW(),
        vet_id      INTEGER,
        case_id     INTEGER,
        actor       TEXT,
        action      TEXT NOT NULL,
        detail      TEXT
    )""", fetch=False)
    q("CREATE INDEX IF NOT EXISTS idx_vet_cases_state_status ON vet_cases(state, status)",
      fetch=False)
    q("CREATE INDEX IF NOT EXISTS idx_vet_licenses_vet ON vet_licenses(vet_id)", fetch=False)


def audit(q, action, actor="", vet_id=None, case_id=None, detail=None):
    """Append-only. Never updated, never deleted."""
    q("""INSERT INTO vet_audit (vet_id, case_id, actor, action, detail)
         VALUES (%s,%s,%s,%s,%s)""",
      (vet_id, case_id, actor or "", action,
       json.dumps(detail) if detail is not None else None), fetch=False)


# ── who a vet is, and what they may touch ────────────────────────────────────

def vet_for_session(q1):
    """The verified vet record for the logged-in user, or None."""
    uid = session.get("user_id")
    if not uid:
        return None
    return q1("SELECT * FROM vets WHERE user_id=%s", (uid,))


def active_states_for_vet(q, q1, vet_id):
    """States this vet may work in RIGHT NOW.

    The intersection of three things, all of which must hold: they hold a licence there,
    the licence has not expired, and the state is compliance-active. Any one failing
    removes the state — which is what keeps invariants 1 and 2 true by construction rather
    than by remembering to check.
    """
    rows = q("""SELECT state, license_number, expires_on, verified_at
                FROM vet_licenses WHERE vet_id=%s""", (vet_id,)) or []
    today = datetime.now(timezone.utc).date()
    out = []
    for r in rows:
        if not r.get("verified_at"):
            continue
        exp = r.get("expires_on")
        if exp and exp < today:
            continue
        ok, _ = vc.is_allowed(q1, r["state"], vc.ACTION_ROUTE_CASE)
        if ok:
            out.append(r["state"])
    return sorted(set(out))


def require_vet(q1):
    """Decorator: a verified, non-suspended vet only."""
    def deco(f):
        @wraps(f)
        def inner(*a, **kw):
            vet = vet_for_session(q1)
            if not vet:
                return jsonify({"error": "not a registered veterinarian"}), 403
            if vet["status"] != VET_STATUS_VERIFIED:
                return jsonify({"error": f"your account is '{vet['status']}' — a crittr "
                                         f"administrator must verify your licence before "
                                         f"you can see cases"}), 403
            return f(vet, *a, **kw)
        return inner
    return deco


# ── the case flow ────────────────────────────────────────────────────────────

def enqueue_case(q, q1, *, state, ai_verdict, owner_message="", ai_reasoning="",
                 owner_user_id=None, pet_id=None, photo_url=None, source="triage",
                 source_ref=None):
    """Put a triage case in front of a vet — IF that state is open.

    Returns (case_id|None, reason). A refusal here is normal and safe: it means we are not
    permitted to route in that state yet, and the owner keeps the find-a-vet path they
    already had. It must never fall through to routing anyway.
    """
    ok, why = vc.is_allowed(q1, state, vc.ACTION_ROUTE_CASE)
    if not ok:
        return None, why
    row = q1("""INSERT INTO vet_cases
                (state, owner_user_id, pet_id, source, source_ref, ai_verdict,
                 ai_reasoning, owner_message, photo_url, status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'queued') RETURNING id""",
             ((state or "").upper()[:2], owner_user_id, pet_id, source, source_ref,
              ai_verdict, ai_reasoning, owner_message, photo_url))
    cid = row["id"] if row else None
    if cid:
        audit(q, "case_enqueued", actor="system", case_id=cid,
              detail={"state": state, "ai_verdict": ai_verdict})
    return cid, ""


def queue_for_vet(q, q1, vet, limit=50):
    """Unclaimed cases this vet is permitted to see. Empty is a valid, safe answer."""
    states = active_states_for_vet(q, q1, vet["id"])
    if not states:
        return [], ("you have no verified, unexpired licence in a state crittr is "
                    "currently permitted to route cases in")
    rows = q(f"""SELECT * FROM vet_cases
                 WHERE status='queued' AND state = ANY(%s)
                 ORDER BY
                   CASE ai_verdict WHEN 'ER NOW' THEN 0 WHEN 'VET TOMORROW' THEN 1
                        ELSE 2 END,
                   created_at ASC
                 LIMIT %s""", (states, int(limit))) or []
    return rows, ""


def claim_case(q, q1, vet, case_id):
    """Take a case. Refuses anything outside the vet's permitted states."""
    case = q1("SELECT * FROM vet_cases WHERE id=%s", (case_id,))
    if not case:
        return None, "no such case"
    if case["status"] != CASE_QUEUED:
        return None, f"that case is already '{case['status']}'"
    if case["state"] not in active_states_for_vet(q, q1, vet["id"]):
        return None, (f"you are not licensed to practise in {case['state']}, or crittr is "
                      f"not yet permitted to route cases there")
    q("""UPDATE vet_cases SET status='claimed', assigned_vet_id=%s, claimed_at=NOW()
         WHERE id=%s AND status='queued'""", (vet["id"], case_id), fetch=False)
    audit(q, "case_claimed", actor=vet["email"], vet_id=vet["id"], case_id=case_id)
    return q1("SELECT * FROM vet_cases WHERE id=%s", (case_id,)), ""


def review_case(q, q1, vet, case_id, vet_verdict, notes="", agreed=None):
    """Record the vet's clinical judgement.

    `agreed` is stored separately from the verdict so the AI's override rate is a measured
    number. If the triage is unsafe, that number is where it shows up first — before a
    complaint does.
    """
    case = q1("SELECT * FROM vet_cases WHERE id=%s", (case_id,))
    if not case:
        return None, "no such case"
    if case.get("assigned_vet_id") != vet["id"]:
        return None, "that case is not assigned to you"
    if agreed is None and vet_verdict and case.get("ai_verdict"):
        agreed = (str(vet_verdict).strip().upper() ==
                  str(case["ai_verdict"]).strip().upper())
    q("""UPDATE vet_cases
         SET status='reviewed', vet_verdict=%s, vet_notes=%s, agreed_with_ai=%s,
             reviewed_at=NOW()
         WHERE id=%s""",
      (vet_verdict, notes, agreed, case_id), fetch=False)
    audit(q, "case_reviewed", actor=vet["email"], vet_id=vet["id"], case_id=case_id,
          detail={"vet_verdict": vet_verdict, "agreed_with_ai": agreed,
                  "ai_verdict": case.get("ai_verdict")})
    return q1("SELECT * FROM vet_cases WHERE id=%s", (case_id,)), ""


def establish_vcpr(q, q1, vet, owner_user_id, pet_id, method, state):
    """Record a VCPR — only where that method is permitted in that state.

    This is the function that decides whether crittr can be a prescribing platform in a
    state or only a triage one, so it asks vet_compliance rather than deciding anything
    itself. Telemedicine-established VCPRs are gated; an in-person one is recorded on the
    vet's attestation that the examination happened.
    """
    method = (method or "").strip().lower()
    if method not in ("in_person", "telemedicine"):
        return None, "method must be 'in_person' or 'telemedicine'"
    if method == "telemedicine":
        ok, why = vc.is_allowed(q1, state, vc.ACTION_ESTABLISH_VCPR_REMOTE)
        if not ok:
            return None, why
    rules = vc.get_state(q1, state) or {}
    days = rules.get("vcpr_valid_days")
    expires = None
    if days:
        expires = datetime.now(timezone.utc) + timedelta(days=int(days))
    q("""INSERT INTO vcpr_records
         (vet_id, owner_user_id, pet_id, state, method, expires_at, established_by)
         VALUES (%s,%s,%s,%s,%s,%s,%s)
         ON CONFLICT (vet_id, owner_user_id, pet_id)
         DO UPDATE SET method=EXCLUDED.method, established_at=NOW(),
                       expires_at=EXCLUDED.expires_at, revoked_at=NULL""",
      (vet["id"], owner_user_id, pet_id, (state or "").upper()[:2], method, expires,
       vet["email"]), fetch=False)
    audit(q, "vcpr_established", actor=vet["email"], vet_id=vet["id"],
          detail={"owner_user_id": owner_user_id, "pet_id": pet_id, "method": method,
                  "state": state, "expires_at": str(expires) if expires else None})
    return q1("""SELECT * FROM vcpr_records WHERE vet_id=%s AND owner_user_id=%s
                 AND pet_id IS NOT DISTINCT FROM %s""",
              (vet["id"], owner_user_id, pet_id)), ""


def vcpr_status(q1, vet_id, owner_user_id, pet_id):
    """Is there a live VCPR? Expiry and revocation both count as no."""
    row = q1("""SELECT * FROM vcpr_records WHERE vet_id=%s AND owner_user_id=%s
                AND pet_id IS NOT DISTINCT FROM %s""",
             (vet_id, owner_user_id, pet_id))
    if not row:
        return {"valid": False, "reason": "no VCPR on record"}
    if row.get("revoked_at"):
        return {"valid": False, "reason": "VCPR was revoked"}
    exp = row.get("expires_at")
    if exp and exp < datetime.now(timezone.utc):
        return {"valid": False, "reason": f"VCPR expired {exp:%Y-%m-%d}"}
    return {"valid": True, "reason": "", "method": row.get("method"),
            "established_at": str(row.get("established_at"))}


def may_prescribe(q, q1, vet, owner_user_id, pet_id, state, controlled=False):
    """The full prescribing gate: state rules AND a live VCPR AND a licence there.

    Three independent conditions, all required. Kept in one function so no caller can
    accidentally check two of them and ship the third as an assumption.
    """
    action = vc.ACTION_PRESCRIBE_CONTROLLED if controlled else vc.ACTION_PRESCRIBE
    ok, why = vc.is_allowed(q1, state, action)
    if not ok:
        return False, why
    if state not in active_states_for_vet(q, q1, vet["id"]):
        return False, f"you hold no verified, unexpired licence in {state}"
    v = vcpr_status(q1, vet["id"], owner_user_id, pet_id)
    if not v["valid"]:
        return False, v["reason"]
    return True, ""


# ── HTTP surface ─────────────────────────────────────────────────────────────

def register_vet_routes(app, q, q1, admin_required):
    """Wire the vet portal. `admin_required` is the app's existing admin decorator."""

    vet_only = require_vet(q1)

    @app.route("/api/vet/apply", methods=["POST"])
    def vet_apply():
        """A veterinarian applies. Creates a PENDING record — never auto-verified."""
        uid = session.get("user_id")
        if not uid:
            return jsonify({"error": "sign in first"}), 401
        d = request.get_json(silent=True) or {}
        name = (d.get("full_name") or "").strip()
        state = (d.get("state") or "").strip().upper()[:2]
        lic = (d.get("license_number") or "").strip()
        if not (name and state and lic):
            return jsonify({"error": "full_name, state and license_number are required"}), 400
        existing = q1("SELECT * FROM vets WHERE user_id=%s", (uid,))
        if existing:
            vet_id = existing["id"]
        else:
            row = q1("""INSERT INTO vets (user_id, full_name, clinic_name, email, phone,
                                          status)
                        VALUES (%s,%s,%s,%s,%s,'pending') RETURNING id""",
                     (uid, name, (d.get("clinic_name") or "").strip(),
                      (d.get("email") or "").strip(), (d.get("phone") or "").strip()))
            vet_id = row["id"] if row else None
        q("""INSERT INTO vet_licenses (vet_id, state, license_number, expires_on)
             VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
          (vet_id, state, lic, d.get("expires_on") or None), fetch=False)
        audit(q, "vet_applied", actor=(d.get("email") or ""), vet_id=vet_id,
              detail={"state": state})
        return jsonify({"ok": True, "vet_id": vet_id, "status": "pending",
                        "next": "a crittr administrator verifies your licence with the "
                                "state board before you can see cases"})

    @app.route("/api/vet/me", methods=["GET"])
    @vet_only
    def vet_me(vet):
        states = active_states_for_vet(q, q1, vet["id"])
        return jsonify({"vet": {k: str(v) for k, v in vet.items() if k != "notes"},
                        "active_states": states,
                        "can_see_cases": bool(states)})

    @app.route("/api/vet/cases", methods=["GET"])
    @vet_only
    def vet_cases(vet):
        rows, why = queue_for_vet(q, q1, vet)
        return jsonify({"cases": [{k: str(v) for k, v in r.items()} for r in rows],
                        "count": len(rows), "note": why})

    @app.route("/api/vet/cases/<int:case_id>/claim", methods=["POST"])
    @vet_only
    def vet_claim(vet, case_id):
        case, why = claim_case(q, q1, vet, case_id)
        return (jsonify({"ok": True, "case": {k: str(v) for k, v in case.items()}})
                if case else (jsonify({"error": why}), 403))

    @app.route("/api/vet/cases/<int:case_id>/review", methods=["POST"])
    @vet_only
    def vet_review(vet, case_id):
        d = request.get_json(silent=True) or {}
        verdict = (d.get("verdict") or "").strip()
        if not verdict:
            return jsonify({"error": "verdict is required"}), 400
        case, why = review_case(q, q1, vet, case_id, verdict,
                                notes=(d.get("notes") or "").strip(),
                                agreed=d.get("agreed"))
        return (jsonify({"ok": True, "case": {k: str(v) for k, v in case.items()}})
                if case else (jsonify({"error": why}), 403))

    @app.route("/api/vet/vcpr", methods=["POST"])
    @vet_only
    def vet_vcpr(vet):
        d = request.get_json(silent=True) or {}
        rec, why = establish_vcpr(q, q1, vet, d.get("owner_user_id"), d.get("pet_id"),
                                  d.get("method"), d.get("state"))
        return (jsonify({"ok": True, "vcpr": {k: str(v) for k, v in rec.items()}})
                if rec else (jsonify({"error": why}), 403))

    # ── admin: verification and the state rollout ────────────────────────────

    @app.route("/api/admin/vets", methods=["GET"])
    @admin_required
    def admin_vets():
        rows = q("""SELECT v.*, (SELECT json_agg(l) FROM vet_licenses l
                                 WHERE l.vet_id=v.id) AS licenses
                    FROM vets v ORDER BY v.created_at DESC""") or []
        return jsonify({"vets": [{k: str(v) for k, v in r.items()} for r in rows]})

    @app.route("/api/admin/vets/<int:vet_id>/verify", methods=["POST"])
    @admin_required
    def admin_verify_vet(vet_id):
        """Verify a vet's licence. A HUMAN act, recorded with their name."""
        d = request.get_json(silent=True) or {}
        actor = (d.get("actor") or session.get("user_email") or "admin").strip()
        q("""UPDATE vets SET status=%s, verified_by=%s, verified_at=NOW(), notes=%s
             WHERE id=%s""",
          (VET_STATUS_VERIFIED, actor, (d.get("notes") or ""), vet_id), fetch=False)
        q("""UPDATE vet_licenses SET verified_by=%s, verified_at=NOW() WHERE vet_id=%s""",
          (actor, vet_id), fetch=False)
        audit(q, "vet_verified", actor=actor, vet_id=vet_id, detail=d)
        return jsonify({"ok": True, "vet_id": vet_id, "status": VET_STATUS_VERIFIED,
                        "verified_by": actor})

    @app.route("/api/admin/compliance/<state>", methods=["GET"])
    @admin_required
    def admin_state_checklist(state):
        """What still blocks this state — the rollout worklist, one state at a time."""
        vc.seed_state(q, state)
        return jsonify(vc.state_checklist(q1, state))

    @app.route("/api/admin/compliance/<state>/activate", methods=["POST"])
    @admin_required
    def admin_activate_state(state):
        """Record a named human's answers and switch a state on."""
        d = request.get_json(silent=True) or {}
        actor = (d.get("actor") or "").strip()
        if not actor:
            return jsonify({"error": "actor is required — name the person (vet or "
                                     "attorney) whose determination this is"}), 400
        try:
            row = vc.activate_state(q, q1, state, d.get("answers") or {}, actor,
                                    d.get("note") or "")
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"ok": True, "state": vc.state_checklist(q1, state),
                        "row": {k: str(v) for k, v in (row or {}).items()}})

    @app.route("/api/admin/compliance/<state>/suspend", methods=["POST"])
    @admin_required
    def admin_suspend_state(state):
        d = request.get_json(silent=True) or {}
        vc.suspend_state(q, state, (d.get("actor") or "admin"), d.get("reason") or "")
        return jsonify({"ok": True, "state": vc.state_checklist(q1, state)})
