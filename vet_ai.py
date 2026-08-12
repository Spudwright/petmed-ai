"""crittr.ai — the chart assistant: AI over a vet's OWN clients.

WHY A VET WOULD SIGN UP FOR THIS AND NOT JUST THE MONEY. Revenue share gets a clinic to
listen; it does not get them to log in. What gets them to log in is that crittr knows things
about their patient that their own PIMS does not: whether the owner actually gave the doses,
what they said at 2am before they called, what they have and have not re-ordered. A practice
management system records what happened in the building. This records what happened at home,
which is where the other three hundred and sixty days are.

So this is deliberately NOT a second opinion. It answers questions about ONE animal from the
record crittr already holds, and every claim it makes has to come from that record. A vet
does not need a language model's view on otitis; they need to know, in four seconds and
without reading twelve screens, that this owner stopped the drops on day three.

THE ACCESS RULE, which is the only thing here that could genuinely hurt someone. A vet may
read a chart if and only if one of two things is true:

  * the owner is a CLAIMED client of that vet's practice, or
  * there is a live VCPR between that vet and that animal

Nothing else grants access — not being licensed in the state, not having seen a triage case
go past, not being verified. Assembling the chart is gated once, at the top, and every route
goes through that gate. A vet browsing a stranger's animal's history is the failure mode
this file exists to prevent.

WHAT IT WILL NOT DO. It will not diagnose, will not dose, and will not recommend a
prescription — not because a model could not produce that text, but because the moment it
does, crittr is practising veterinary medicine rather than handing a veterinarian their own
records back. The system prompt says so and the refusal is tested.
"""
import json
import logging
from datetime import datetime, timezone

from flask import request, jsonify

import vet_portal as vp
import vet_practice as vpr
import llm_client

log = logging.getLogger("crittr.vetai")

SYSTEM = """You are a chart assistant inside crittr, used by a licensed veterinarian who is \
looking at their OWN client's record. You are talking to the clinician, not the owner.

Your entire job is to make the record below faster to read. Rules, in order of importance:

1. Every statement you make must be supported by the record. If the record does not say it, \
say that it does not. Never estimate, never infer a value that is not there, and never fill \
a gap with what is typically true of this breed or condition.
2. Do NOT diagnose, do NOT recommend or adjust a medication or dose, and do NOT suggest a \
prescription. The veterinarian does that. If asked, say plainly that you summarise the \
record and they make the call.
3. Lead with what changed or what is off — a missed course, a symptom that recurred, a \
refill never collected. A vet reading this has ninety seconds between appointments.
4. Be concrete about dates and counts. "14 of 20 doses given, none since 4 Aug" is useful; \
"adherence has been suboptimal" is not.
5. If the record is thin, say so in one line rather than padding.

Write in plain sentences, no headings, under 150 words unless asked for more."""

REFUSAL = ("I summarise this patient's record — the prescribing decision is yours. "
           "Here is what the record shows:")


# ── the access gate ──────────────────────────────────────────────────────────

def may_view(q, q1, vet, owner_user_id, pet_id=None):
    """May this vet read this animal's chart? Returns (bool, reason).

    Two doors, both of which mean a real relationship exists. Kept in one function so no
    route can check one of them and ship the other as an assumption — the same reasoning
    vet_portal.may_prescribe uses.
    """
    if not owner_user_id:
        return False, "no owner specified"
    link = vpr.practice_client_for_user(q1, owner_user_id)
    if link and link.get("vet_id") == vet["id"]:
        return True, ""
    v = vp.vcpr_status(q1, vet["id"], owner_user_id, pet_id)
    if v["valid"]:
        return True, ""
    return False, ("this owner is not a connected client of your practice and you have no "
                   "current vet-client-patient relationship with this animal, so their "
                   "record is not yours to read")


# ── assembling the chart ─────────────────────────────────────────────────────

def build_chart(q, q1, vet, owner_user_id, pet_id=None):
    """Everything crittr knows about this animal, as a dict. Access is checked FIRST."""
    ok, why = may_view(q, q1, vet, owner_user_id, pet_id)
    if not ok:
        return None, why

    pet = q1("SELECT * FROM pets WHERE id=%s AND user_id=%s",
             (pet_id, owner_user_id)) if pet_id else None
    pets = q("SELECT * FROM pets WHERE user_id=%s ORDER BY id", (owner_user_id,)) or []

    cases = q("""SELECT id, state, ai_verdict, vet_verdict, owner_message, agreed_with_ai,
                        created_at, reviewed_at
                 FROM vet_cases
                 WHERE owner_user_id=%s AND (%s::int IS NULL OR pet_id=%s)
                 ORDER BY created_at DESC LIMIT 10""",
              (owner_user_id, pet_id, pet_id)) or []

    plans = q("""SELECT p.id, p.summary, p.status, p.created_at
                 FROM care_plans p
                 WHERE p.owner_user_id=%s AND (%s::int IS NULL OR p.pet_id=%s)
                 ORDER BY p.created_at DESC LIMIT 5""",
              (owner_user_id, pet_id, pet_id)) or []

    items = []
    for p in plans:
        rows = q("""SELECT i.id, i.kind, i.title, i.instructions, i.times_per_day, i.days,
                           i.starts_on,
                           (SELECT COUNT(*) FROM med_doses d WHERE d.item_id=i.id) AS due,
                           (SELECT COUNT(*) FROM med_doses d WHERE d.item_id=i.id
                             AND d.given_at IS NOT NULL) AS given,
                           (SELECT MAX(d.given_at) FROM med_doses d WHERE d.item_id=i.id
                             AND d.given_at IS NOT NULL) AS last_given
                    FROM care_plan_items i WHERE i.plan_id=%s ORDER BY i.id""",
                 (p["id"],)) or []
        for r in rows:
            r = dict(r)
            r["plan_id"] = p["id"]
            items.append(r)

    followups = q("""SELECT id, due_on, status, owner_message, vet_reply, answered_at
                     FROM followups WHERE owner_user_id=%s
                     ORDER BY due_on DESC LIMIT 8""", (owner_user_id,)) or []

    refills = q("""SELECT id, medication, status, note, vet_note, created_at, decided_at
                   FROM refill_requests WHERE owner_user_id=%s
                   ORDER BY created_at DESC LIMIT 8""", (owner_user_id,)) or []

    # What they actually bought is the honest adherence signal: a course they never
    # re-ordered was a course they stopped, whatever the dose log says.
    orders = q("""SELECT id, items, total_cents, created_at FROM orders
                  WHERE user_id=%s AND status='paid'
                  ORDER BY created_at DESC LIMIT 10""", (owner_user_id,)) or []

    return {
        "pet": dict(pet) if pet else None,
        "pets": [dict(p) for p in pets],
        "vcpr": vp.vcpr_status(q1, vet["id"], owner_user_id, pet_id),
        "triage_cases": [dict(c) for c in cases],
        "care_plans": [dict(p) for p in plans],
        "plan_items": items,
        "followups": [dict(f) for f in followups],
        "refills": [dict(r) for r in refills],
        "orders": [dict(o) for o in orders],
    }, ""


def _fmt_date(v):
    if not v:
        return "—"
    if isinstance(v, str):
        return v[:10]
    try:
        return v.strftime("%d %b %Y")
    except Exception:
        return str(v)[:10]


def render_chart(chart):
    """The chart as text for the model. Written to be readable by a human too, because a
    prompt nobody can read is a prompt nobody can debug."""
    L = []
    pet = chart.get("pet")
    if pet:
        bits = [pet.get("name") or "unnamed", pet.get("species") or "", pet.get("breed") or ""]
        if pet.get("age_years"):
            bits.append(f"{pet['age_years']}y")
        if pet.get("weight_lbs"):
            bits.append(f"{pet['weight_lbs']}lb")
        L.append("PATIENT: " + ", ".join(b for b in bits if b))
        if pet.get("conditions"):
            L.append(f"Known conditions: {pet['conditions']}")
    elif chart.get("pets"):
        L.append("PATIENTS ON THIS ACCOUNT: " +
                 "; ".join(f"{p.get('name')} ({p.get('species')})" for p in chart["pets"]))

    v = chart.get("vcpr") or {}
    L.append(f"VCPR: {'valid' if v.get('valid') else 'none on record'}"
             + (f" — {v.get('method')} on {_fmt_date(v.get('established_at'))}"
                if v.get("valid") else f" — {v.get('reason','')}"))

    if chart.get("triage_cases"):
        L.append("\nTRIAGE HISTORY (owner's words at the time):")
        for c in chart["triage_cases"]:
            L.append(f"  {_fmt_date(c.get('created_at'))} — crittr said "
                     f"{c.get('ai_verdict') or '—'}"
                     + (f", vet said {c['vet_verdict']}" if c.get("vet_verdict") else "")
                     + f". Owner: \"{(c.get('owner_message') or '')[:220]}\"")

    if chart.get("care_plans"):
        L.append("\nCARE PLANS:")
        for p in chart["care_plans"]:
            L.append(f"  #{p['id']} {_fmt_date(p.get('created_at'))} [{p.get('status')}] "
                     f"{p.get('summary') or ''}")

    if chart.get("plan_items"):
        L.append("\nWHAT WAS PRESCRIBED, AND WHETHER IT WAS GIVEN:")
        for i in chart["plan_items"]:
            line = f"  [{i.get('kind')}] {i.get('title') or ''}"
            if i.get("times_per_day") and i.get("days"):
                line += f" — {i['times_per_day']}x/day for {i['days']} days"
            due, given = int(i.get("due") or 0), int(i.get("given") or 0)
            if due:
                line += f" — {given} of {due} doses marked given"
                line += (f", last on {_fmt_date(i.get('last_given'))}"
                         if i.get("last_given") else ", NONE marked given")
            L.append(line)

    if chart.get("followups"):
        L.append("\nFOLLOW-UPS:")
        for f in chart["followups"]:
            L.append(f"  due {_fmt_date(f.get('due_on'))} [{f.get('status')}]"
                     + (f" — owner: \"{(f.get('owner_message') or '')[:180]}\""
                        if f.get("owner_message") else "")
                     + (f" — vet replied: \"{(f.get('vet_reply') or '')[:180]}\""
                        if f.get("vet_reply") else ""))

    if chart.get("refills"):
        L.append("\nREFILL REQUESTS:")
        for r in chart["refills"]:
            L.append(f"  {_fmt_date(r.get('created_at'))} {r.get('medication')} "
                     f"[{r.get('status')}]"
                     + (f" — {r.get('vet_note')}" if r.get("vet_note") else ""))

    if chart.get("orders"):
        L.append("\nWHAT THEY ACTUALLY BOUGHT (re-orders are the real adherence signal):")
        for o in chart["orders"]:
            its = o.get("items")
            if isinstance(its, str):
                try:
                    its = json.loads(its)
                except Exception:
                    its = []
            names = ", ".join(f"{i.get('name')} x{i.get('quantity', 1)}"
                              for i in (its or [])[:6]) or "—"
            L.append(f"  {_fmt_date(o.get('created_at'))} — {names}")

    return "\n".join(L) if L else "The record is empty for this client."


# ── the question ─────────────────────────────────────────────────────────────

def ask(q, q1, vet, *, owner_user_id, pet_id=None, question=""):
    """Answer a clinician's question from this animal's record. Returns (answer, reason)."""
    question = (question or "").strip()
    if not question:
        return None, "ask a question"
    chart, why = build_chart(q, q1, vet, owner_user_id, pet_id)
    if chart is None:
        return None, why
    if not llm_client.has_provider():
        return None, "no LLM provider is configured on this deployment"
    body = (f"RECORD FOR THIS PATIENT\n{render_chart(chart)}\n\n"
            f"THE VETERINARIAN ASKS: {question}")
    try:
        answer = llm_client.generate_summary(SYSTEM, body)
    except Exception as e:                                  # noqa: BLE001
        log.error("[vetai] generation failed: %s", e)
        return None, "the assistant is unavailable right now"
    vp.audit(q, "vet_chart_queried", actor=vet.get("email", ""), vet_id=vet["id"],
             detail={"owner_user_id": owner_user_id, "pet_id": pet_id,
                     "question": question[:300]})
    return answer, ""


def brief(q, q1, vet, *, owner_user_id, pet_id=None):
    """The unprompted version: what a vet should know before this animal walks in."""
    return ask(q, q1, vet, owner_user_id=owner_user_id, pet_id=pet_id,
               question="What should I know before I see this animal today? Lead with "
                       "anything that has gone wrong since the last visit.")


# ── HTTP surface ─────────────────────────────────────────────────────────────

def register_vet_ai_routes(app, q, q1):
    vet_only = vp.require_vet(q1)

    @app.route("/api/vet/chart", methods=["GET"])
    @vet_only
    def api_vet_chart(vet):
        owner = request.args.get("owner_user_id", type=int)
        pet = request.args.get("pet_id", type=int)
        chart, why = build_chart(q, q1, vet, owner, pet)
        if chart is None:
            return jsonify({"error": why}), 403
        return jsonify({"chart": chart, "rendered": render_chart(chart)}, )

    @app.route("/api/vet/chart/ask", methods=["POST"])
    @vet_only
    def api_vet_chart_ask(vet):
        d = request.get_json(silent=True) or {}
        answer, why = ask(q, q1, vet, owner_user_id=d.get("owner_user_id"),
                          pet_id=d.get("pet_id"), question=d.get("question"))
        if answer is None:
            return jsonify({"error": why}), 403 if "not yours" in why else 400
        return jsonify({"ok": True, "answer": answer})

    @app.route("/api/vet/chart/brief", methods=["POST"])
    @vet_only
    def api_vet_chart_brief(vet):
        d = request.get_json(silent=True) or {}
        answer, why = brief(q, q1, vet, owner_user_id=d.get("owner_user_id"),
                            pet_id=d.get("pet_id"))
        if answer is None:
            return jsonify({"error": why}), 403 if "not yours" in why else 400
        return jsonify({"ok": True, "brief": answer})

    @app.route("/api/vet/clients/connected", methods=["GET"])
    @vet_only
    def api_connected_clients(vet):
        """The vet's own connected clients, with their pets — the chart picker's source."""
        practice = vpr.practice_for_vet(q1, vet["id"])
        if not practice:
            return jsonify({"clients": []})
        rows = q("""SELECT c.id, c.owner_name, c.pet_name, c.email, c.user_id,
                           c.claimed_at
                    FROM practice_clients c
                    WHERE c.practice_id=%s AND c.status='claimed'
                    ORDER BY c.claimed_at DESC LIMIT 300""", (practice["id"],)) or []
        out = []
        for r in rows:
            r = dict(r)
            r["pets"] = [dict(p) for p in
                         (q("SELECT id, name, species FROM pets WHERE user_id=%s",
                            (r["user_id"],)) or [])]
            r.pop("email", None)        # the chart picker does not need it
            out.append(r)
        return jsonify({"clients": [vpr._ser(r) if not isinstance(r, dict) else
                                    {k: (v if isinstance(v, (int, list, type(None)))
                                         else str(v)) for k, v in r.items()}
                                    for r in out]})
