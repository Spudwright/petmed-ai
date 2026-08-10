"""crittr.ai — per-state veterinary compliance rules.

THIS MODULE IS THE FOUNDATION OF THE STATE-BY-STATE ROLLOUT. Everything the vet side is
allowed to do in a given state is decided here, so launching a new state is a data change
plus a human sign-off — never a code change.

THE ONE RULE THAT MATTERS: DEFAULT DENY.
A state we have not explicitly reviewed and switched on cannot receive vet routing, cannot
establish a VCPR and cannot support a prescription. "We haven't checked yet" and "it's
allowed" must never be the same value, because the failure mode is practising veterinary
medicine somewhere we are not permitted to.

NOTHING IN THIS FILE IS LEGAL ADVICE, AND NO RULE HERE IS ASSERTED BY THE SOFTWARE.
Every state ships as `unverified` with its questions unanswered. A named human — the
partner veterinarian or an attorney — answers them and records that answer with their name
against a date. The code's only opinions are structural: default deny, expiry is real, and
an unanswered question blocks the action it governs.

WHY VCPR IS THE HINGE. A Veterinarian-Client-Patient Relationship is the legal
precondition for a vet to diagnose or prescribe. States differ on whether telemedicine
alone can establish one, or whether an in-person examination must come first, and that
single answer decides whether crittr can be a prescribing platform in a state or only a
triage-and-booking platform. It is therefore the first field on every state record and the
gate on every privileged action.
"""
import os
import json
import logging
from datetime import datetime, timezone

log = logging.getLogger("crittr.compliance")

# Actions the vet side can take. Each is gated on a specific answered question, so a state
# can be partially open — triage routing on, prescribing off — which is very likely the
# real shape of a first launch.
ACTION_ROUTE_CASE = "route_case"          # send an owner's triage case to a partner vet
ACTION_ESTABLISH_VCPR_REMOTE = "vcpr_remote"   # establish a VCPR by telemedicine alone
ACTION_PRESCRIBE = "prescribe"            # issue a prescription
ACTION_PRESCRIBE_CONTROLLED = "prescribe_controlled"

ACTIONS = (ACTION_ROUTE_CASE, ACTION_ESTABLISH_VCPR_REMOTE,
           ACTION_PRESCRIBE, ACTION_PRESCRIBE_CONTROLLED)

# The questions a state record must answer before each action is permitted. Unanswered
# (None) is treated as NO — see default deny above.
_GATES = {
    ACTION_ROUTE_CASE: ("routing_allowed",),
    ACTION_ESTABLISH_VCPR_REMOTE: ("telemedicine_vcpr_allowed",),
    ACTION_PRESCRIBE: ("rx_allowed", "vcpr_required_for_rx_satisfied"),
    ACTION_PRESCRIBE_CONTROLLED: ("rx_allowed", "controlled_allowed"),
}

STATUS_UNVERIFIED = "unverified"    # never reviewed — everything denied
STATUS_ACTIVE = "active"            # reviewed and signed off by a named human
STATUS_SUSPENDED = "suspended"      # switched off deliberately (kill switch)


def init_compliance_tables(q):
    """Create the state-rules table. Safe to call repeatedly."""
    q("""
    CREATE TABLE IF NOT EXISTS vet_state_rules (
        state                          CHAR(2) PRIMARY KEY,
        status                         TEXT NOT NULL DEFAULT 'unverified',
        -- Each of these is TRUE / FALSE / NULL. NULL means "not answered yet" and is
        -- treated as FALSE everywhere. They are deliberately separate booleans rather than
        -- one "compliant" flag, because a state can permit triage routing while forbidding
        -- remote prescribing, and collapsing that into one field loses the distinction the
        -- whole rollout depends on.
        routing_allowed                BOOLEAN,
        telemedicine_vcpr_allowed      BOOLEAN,
        vcpr_required_for_rx_satisfied BOOLEAN,
        rx_allowed                     BOOLEAN,
        controlled_allowed             BOOLEAN,
        -- VCPR lifetime. Several states expire a relationship after a period without an
        -- examination; 0/NULL means we do not expire it ourselves.
        vcpr_valid_days                INTEGER,
        -- Provenance. A rule nobody signed is a rule nobody is accountable for.
        source_note                    TEXT,
        confirmed_by                   TEXT,
        confirmed_at                   TIMESTAMPTZ,
        updated_at                     TIMESTAMPTZ DEFAULT NOW()
    )""", fetch=False)
    q("""
    CREATE TABLE IF NOT EXISTS vet_compliance_audit (
        id          SERIAL PRIMARY KEY,
        at          TIMESTAMPTZ DEFAULT NOW(),
        state       CHAR(2),
        actor       TEXT,
        change      TEXT
    )""", fetch=False)


# The questions each state must answer, in the words a veterinarian or attorney would
# actually be asked. Shipped as prompts, never as answers.
STATE_QUESTIONS = [
    ("telemedicine_vcpr_allowed",
     "Can a valid VCPR be established by telemedicine alone in this state, or is an "
     "in-person physical examination required first?"),
    ("routing_allowed",
     "May we route an owner's triage case to a licensed veterinarian in this state for "
     "review, given the vet is licensed here?"),
    ("rx_allowed",
     "May a veterinarian prescribe on the basis of a telemedicine consultation in this "
     "state, where a valid VCPR exists?"),
    ("vcpr_required_for_rx_satisfied",
     "Does our VCPR record (how we establish and store it) satisfy this state's "
     "requirement for prescribing?"),
    ("controlled_allowed",
     "May controlled substances be prescribed via telemedicine in this state?"),
    ("vcpr_valid_days",
     "How long does a VCPR remain valid without a further examination? (days; blank = "
     "no expiry we need to enforce)"),
]


def seed_state(q, state, note=""):
    """Create a state record in the DENIED state, with every question unanswered.

    Deliberately does not guess. New Mexico is seeded by the migration exactly like every
    other state — unverified, everything off — because the author of this code is not
    qualified to answer a single one of the questions above and guessing would be the most
    dangerous thing this file could do.
    """
    state = (state or "").strip().upper()[:2]
    if len(state) != 2:
        raise ValueError("state must be a 2-letter code")
    q("""INSERT INTO vet_state_rules (state, status, source_note)
         VALUES (%s, 'unverified', %s)
         ON CONFLICT (state) DO NOTHING""", (state, note or ""), fetch=False)
    return state


def get_state(q1, state):
    return q1("SELECT * FROM vet_state_rules WHERE state=%s",
              ((state or "").strip().upper()[:2],))


def is_allowed(q1, state, action):
    """(allowed: bool, reason: str). The single gate every privileged path calls.

    Returns a REASON on refusal, not just False, because the operator needs to know which
    unanswered question is blocking them — that is the actionable half of the answer and
    it is what turns this from a wall into a checklist.
    """
    code = (state or "").strip().upper()[:2]
    if action not in _GATES:
        return False, f"unknown action '{action}'"
    if len(code) != 2:
        return False, "no state supplied — cannot evaluate compliance"
    row = get_state(q1, code)
    if not row:
        return False, (f"{code} has no compliance record — every state is denied until it "
                       f"is reviewed and signed off")
    status = (row.get("status") or STATUS_UNVERIFIED)
    if status == STATUS_SUSPENDED:
        return False, f"{code} is suspended"
    if status != STATUS_ACTIVE:
        return False, (f"{code} is '{status}' — a named human must review and activate it "
                       f"before any vet action is permitted there")
    for field in _GATES[action]:
        if row.get(field) is not True:
            answered = "answered no" if row.get(field) is False else "not answered"
            return False, (f"{code}: '{field}' is {answered} — "
                           f"'{dict(STATE_QUESTIONS).get(field, field)}'")
    return True, ""


def activate_state(q, q1, state, answers, actor, note=""):
    """Record a human's answers and switch a state on.

    `actor` is required and stored: a compliance decision with no name against it is not a
    decision. This is the ONLY path that can set status='active'.
    """
    code = (state or "").strip().upper()[:2]
    if not (actor or "").strip():
        raise ValueError("actor is required — a compliance decision must be attributable")
    if not get_state(q1, code):
        seed_state(q, code)
    fields, params = [], []
    for key, _ in STATE_QUESTIONS:
        if key in answers:
            v = answers[key]
            if key == "vcpr_valid_days":
                v = int(v) if str(v).strip() not in ("", "None", "null") else None
            else:
                v = None if v is None else bool(v)
            fields.append(f"{key}=%s")
            params.append(v)
    fields += ["status=%s", "confirmed_by=%s", "confirmed_at=NOW()", "updated_at=NOW()",
               "source_note=%s"]
    params += [STATUS_ACTIVE, actor.strip(), note or ""]
    params.append(code)
    q(f"UPDATE vet_state_rules SET {', '.join(fields)} WHERE state=%s",
      tuple(params), fetch=False)
    q("INSERT INTO vet_compliance_audit (state, actor, change) VALUES (%s,%s,%s)",
      (code, actor.strip(), json.dumps({"activated": True, "answers": answers,
                                        "note": note})), fetch=False)
    return get_state(q1, code)


def suspend_state(q, state, actor, reason=""):
    """Kill switch. Instant, reversible, and it stops routing everywhere in that state."""
    code = (state or "").strip().upper()[:2]
    q("UPDATE vet_state_rules SET status=%s, updated_at=NOW() WHERE state=%s",
      (STATUS_SUSPENDED, code), fetch=False)
    q("INSERT INTO vet_compliance_audit (state, actor, change) VALUES (%s,%s,%s)",
      (code, actor or "system", json.dumps({"suspended": True, "reason": reason})),
      fetch=False)


def state_checklist(q1, state):
    """What still blocks this state, as a list a human can work through."""
    code = (state or "").strip().upper()[:2]
    row = get_state(q1, code) or {}
    out = []
    for key, question in STATE_QUESTIONS:
        v = row.get(key)
        out.append({"field": key, "question": question,
                    "answer": v,
                    "blocking": (v is not True) if key != "vcpr_valid_days" else False})
    return {"state": code, "status": row.get("status") or "missing",
            "confirmed_by": row.get("confirmed_by"),
            "confirmed_at": str(row.get("confirmed_at") or "") or None,
            "questions": out,
            "actions": {a: dict(zip(("allowed", "reason"), is_allowed(q1, code, a)))
                        for a in ACTIONS}}
