"""crittr.ai — Pet profile CRUD + pet-scoped chat memory.

Design notes:
    * Extends (does not replace) the existing `pets` table in init_db() —
      missing columns are added via idempotent ALTER TABLE ... ADD COLUMN IF
      NOT EXISTS, same pattern as ensure_stripe_schema() in stripe_routes.py.
    * Per-pet chat messages go in pet_chat_messages. Rolled-up summaries go
      in pet_chat_summaries — once N new messages accumulate past the last
      summary, _maybe_summarize_pet_chat folds the older ones up, keeping the
      most recent SUMMARIZE_KEEP_RECENT messages in their original form.
    * All routes require login; all data scoped to session["user_id"].
    * LLM call goes through llm_client.py (Anthropic-first, OpenAI fallback).
      Degrades gracefully with a friendly message if no provider is configured.

Wiring (add near the bottom of app.py, after register_stripe_routes):

    from pets_routes import register_pets_routes
    register_pets_routes(app, q=q, q1=q1,
                         login_required=login_required, get_db=get_db)
"""
import json as _json
import logging
from datetime import date
from flask import jsonify, request, session

try:
    from llm_client import generate_chat_reply, generate_summary, has_provider
except Exception:  # pragma: no cover — llm_client is optional at import time
    generate_chat_reply = None
    generate_summary = None
    has_provider = lambda: False  # noqa: E731

log = logging.getLogger("crittr.pets")

_q = None
_q1 = None
_get_db = None
_login_required = None

# Stream 2 tuning
CHAT_HISTORY_TURNS = 10          # last N messages fed to the LLM
SUMMARIZE_EVERY = 20             # roll up into a summary every N new messages
SUMMARIZE_KEEP_RECENT = 8        # always keep last N messages out of summary


# ---------------------- schema ----------------------

def ensure_pets_schema():
    """Create pets / pet_chat_messages / pet_chat_summaries. Idempotent."""
    _q("""
        CREATE TABLE IF NOT EXISTS pets (
            id SERIAL PRIMARY KEY,
            user_id INT REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            species TEXT,
            breed TEXT,
            sex TEXT,
            birth_date DATE,
            weight_lbs NUMERIC(5,2),
            color TEXT,
            photo_url TEXT,
            allergies JSONB DEFAULT '[]',
            conditions JSONB DEFAULT '[]',
            medications JSONB DEFAULT '[]',
            notes TEXT DEFAULT '',
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
    """, fetch=False)

    # If init_db() shipped a leaner pets table, add any missing columns.
    extra_columns = [
        ("species", "TEXT"),
        ("breed", "TEXT"),
        ("sex", "TEXT"),
        ("birth_date", "DATE"),
        ("weight_lbs", "NUMERIC(5,2)"),
        ("color", "TEXT"),
        ("photo_url", "TEXT"),
        ("allergies", "JSONB DEFAULT '[]'"),
        ("conditions", "JSONB DEFAULT '[]'"),
        ("medications", "JSONB DEFAULT '[]'"),
        ("notes", "TEXT DEFAULT ''"),
        ("is_active", "BOOLEAN DEFAULT TRUE"),
        ("updated_at", "TIMESTAMPTZ DEFAULT NOW()"),
    ]
    for col, ddl in extra_columns:
        _q(f"ALTER TABLE pets ADD COLUMN IF NOT EXISTS {col} {ddl};", fetch=False)

    _q("CREATE INDEX IF NOT EXISTS idx_pets_user ON pets(user_id, is_active);", fetch=False)

    _q("""
        CREATE TABLE IF NOT EXISTS pet_chat_messages (
            id SERIAL PRIMARY KEY,
            pet_id INT REFERENCES pets(id) ON DELETE CASCADE,
            user_id INT REFERENCES users(id) ON DELETE CASCADE,
            role TEXT NOT NULL,        -- 'user' | 'assistant' | 'system'
            content TEXT NOT NULL,
            metadata JSONB DEFAULT '{}',
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
    """, fetch=False)
    _q("CREATE INDEX IF NOT EXISTS idx_pet_chat_pet ON pet_chat_messages(pet_id, id);", fetch=False)

    _q("""
        CREATE TABLE IF NOT EXISTS pet_chat_summaries (
            id SERIAL PRIMARY KEY,
            pet_id INT REFERENCES pets(id) ON DELETE CASCADE,
            summary TEXT NOT NULL,
            messages_through INT NOT NULL,  -- last pet_chat_messages.id covered
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
    """, fetch=False)


# ---------------------- constants + helpers ----------------------

_SPECIES_OPTIONS = {
    "dog", "cat", "rabbit", "ferret", "bird", "reptile", "fish",
    "small_mammal", "horse", "other",
}
_SEX_OPTIONS = {
    "male", "female", "male_neutered", "female_spayed", "unknown",
}
_PATCHABLE_FIELDS = {
    "name", "species", "breed", "sex", "birth_date", "weight_lbs",
    "color", "photo_url", "allergies", "conditions", "medications", "notes",
}


def _jsonable(v, fallback="[]"):
    """Turn list/dict/string inputs into a JSON string for ::jsonb casting."""
    if v is None:
        return fallback
    if isinstance(v, (list, dict)):
        return _json.dumps(v)
    if isinstance(v, str):
        try:
            _json.loads(v)
            return v
        except Exception:
            parts = [p.strip() for p in v.split(",") if p.strip()]
            return _json.dumps(parts)
    return fallback


def _dict_pet(row):
    """Serialize a pets row for JSON responses."""
    if not row:
        return None
    d = dict(row)
    if d.get("weight_lbs") is not None:
        d["weight_lbs"] = float(d["weight_lbs"])
    if d.get("birth_date"):
        d["birth_date"] = d["birth_date"].isoformat()
    for ts in ("created_at", "updated_at"):
        if d.get(ts):
            d[ts] = d[ts].isoformat()
    d["age_years"] = _age_years(row)
    return d


def _age_years(row):
    bd = row.get("birth_date")
    if not bd:
        return None
    today = date.today()
    yrs = today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))
    return max(0, yrs)


def _pet_row_for_user(pet_id, user_id):
    return _q1(
        "SELECT * FROM pets WHERE id=%s AND user_id=%s AND is_active=TRUE;",
        (pet_id, user_id),
    )


def _normalize_species(v):
    v = (v or "").strip().lower() or None
    if v and v not in _SPECIES_OPTIONS:
        v = "other"
    return v


def _normalize_sex(v):
    v = (v or "").strip().lower() or None
    if v and v not in _SEX_OPTIONS:
        v = "unknown"
    return v


# ---------------------- chat context builder ----------------------

def build_pet_context(pet_id, user_id, recent_messages=6):
    """Compat helper for debugging / logging — flat string form of context."""
    pet = _pet_row_for_user(pet_id, user_id)
    if not pet:
        return None

    lines = [
        "PET: " + pet["name"]
        + (f" ({pet.get('species')})" if pet.get("species") else "")
        + (f" — {pet.get('breed')}" if pet.get("breed") else ""),
    ]
    if pet.get("sex"):
        lines.append(f"Sex: {pet['sex']}")
    yrs = _age_years(pet)
    if yrs is not None:
        lines.append(f"Age: ~{yrs} yr")
    if pet.get("weight_lbs") is not None:
        lines.append(f"Weight: {float(pet['weight_lbs'])} lb")
    if pet.get("allergies"):
        lines.append("Allergies: " + ", ".join(map(str, pet["allergies"])))
    if pet.get("conditions"):
        lines.append("Known conditions: " + ", ".join(map(str, pet["conditions"])))
    if pet.get("medications"):
        lines.append("Current meds: " + ", ".join(map(str, pet["medications"])))
    if pet.get("notes"):
        lines.append(f"Notes: {pet['notes']}")

    out = "\n".join(lines)

    summary = _q1(
        "SELECT summary FROM pet_chat_summaries WHERE pet_id=%s ORDER BY created_at DESC LIMIT 1;",
        (pet_id,),
    )
    if summary and summary.get("summary"):
        out += "\n\nPRIOR CONVERSATION SUMMARY:\n" + summary["summary"]

    rows = _q(
        "SELECT role, content FROM pet_chat_messages "
        "WHERE pet_id=%s ORDER BY id DESC LIMIT %s;",
        (pet_id, recent_messages),
    ) or []
    if rows:
        recent = list(reversed(rows))
        block = "\n".join(f"[{m['role']}] {m['content']}" for m in recent)
        out += "\n\nRECENT MESSAGES:\n" + block

    return out


# ---------------------- LLM prompt + call ----------------------

CRITTR_SYSTEM_PROMPT_TEMPLATE = """You are Crittr, the AI pet-care companion for crittr.ai.
Voice: warm, compassionate, playful, never clinical. Listen first, ask one
gentle clarifying question before suggesting anything. Keep medical claims
soft; sign medical guidance with: "I'm an AI trained on veterinary research.
Anything prescription is reviewed by a licensed vet."

You have persistent memory of this specific pet:
{PET_CONTEXT}

Rules:
- Ask only ONE clarifying question before any recommendation.
- Urgency signals — bleeding that won't stop, trouble breathing, inability to
  urinate, seizures, collapse, eating something toxic, intense lethargy
  >24h — always urge an immediate in-person vet visit first.
- Never diagnose. Never prescribe. Never claim to replace a vet.
- When suggesting a product, give your reasoning in one sentence.
"""


def _build_system_prompt(pet_id, user_id):
    """Assemble the system prompt: pet profile + prior summary only.

    Recent messages are passed separately via the API's messages array so
    the model treats them as native conversation turns rather than context.
    Returns None if the pet doesn't belong to user_id.
    """
    pet = _pet_row_for_user(pet_id, user_id)
    if not pet:
        return None

    lines = [
        "PET: " + pet["name"]
        + (f" ({pet.get('species')})" if pet.get("species") else "")
        + (f" — {pet.get('breed')}" if pet.get("breed") else ""),
    ]
    if pet.get("sex"):
        lines.append(f"Sex: {pet['sex']}")
    yrs = _age_years(pet)
    if yrs is not None:
        lines.append(f"Age: ~{yrs} yr")
    if pet.get("weight_lbs") is not None:
        lines.append(f"Weight: {float(pet['weight_lbs'])} lb")
    if pet.get("allergies"):
        lines.append("Allergies: " + ", ".join(map(str, pet["allergies"])))
    if pet.get("conditions"):
        lines.append("Known conditions: " + ", ".join(map(str, pet["conditions"])))
    if pet.get("medications"):
        lines.append("Current meds: " + ", ".join(map(str, pet["medications"])))
    if pet.get("notes"):
        lines.append(f"Notes: {pet['notes']}")

    profile_block = "\n".join(lines)

    summary_row = _q1(
        "SELECT summary FROM pet_chat_summaries "
        "WHERE pet_id=%s ORDER BY created_at DESC LIMIT 1;",
        (pet_id,),
    )
    if summary_row and summary_row.get("summary"):
        profile_block += "\n\nPRIOR CONVERSATION SUMMARY:\n" + summary_row["summary"]

    return CRITTR_SYSTEM_PROMPT_TEMPLATE.format(PET_CONTEXT=profile_block)


def _fetch_chat_history(pet_id, limit=CHAT_HISTORY_TURNS):
    """Return the last `limit` messages oldest-first as LLM-ready dicts."""
    rows = _q(
        "SELECT role, content FROM pet_chat_messages "
        "WHERE pet_id=%s ORDER BY id DESC LIMIT %s;",
        (pet_id, limit),
    ) or []
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def _llm_reply(pet_id, user_id, pet_row, user_msg):
    """Call the LLM. Gracefully degrade if no provider is configured."""
    if generate_chat_reply is None or not has_provider():
        name = (pet_row.get("name") if pet_row else None) or "your critter"
        log.info("[pets] no LLM provider configured, returning fallback reply")
        return (
            f"I hear you on {name}. The chat brain is warming up on our side — "
            "give us a moment and try again. If this is urgent, call your vet."
        )

    system_prompt = _build_system_prompt(pet_id, user_id)
    if system_prompt is None:
        return "Hmm, I couldn't find that pet on your account."

    history = _fetch_chat_history(pet_id)
    try:
        return generate_chat_reply(system_prompt, history, user_msg)
    except Exception as e:
        log.warning("[pets] LLM call failed: %s", e)
        name = (pet_row.get("name") if pet_row else None) or "your critter"
        return (
            f"Something hiccuped on my end thinking about {name}. "
            "Mind trying that again in a moment?"
        )


# ---------------------- summarizer (rolling memory) ----------------------

SUMMARY_PROMPT = (
    "You are a careful pet-care conversation summarizer for a pet named {name}."
    " Condense the following chat into a dense, factual memory block for future"
    " LLM calls. Keep:\n"
    "  * symptoms, onset, severity, duration\n"
    "  * anything the vet / pharmacist said\n"
    "  * products, meds, doses, reactions\n"
    "  * concrete commitments (e.g. 'we'll recheck in a week')\n"
    "Drop pleasantries, small talk, and repeated info. Write in 3rd person."
    " Target 8-16 short lines. Do not use markdown."
)


def _maybe_summarize_pet_chat(pet_id, user_id, pet_row):
    """Best-effort summarizer. Rolls older messages into pet_chat_summaries
    once total unsummarized messages exceed SUMMARIZE_EVERY. Never raises."""
    try:
        if generate_summary is None or not has_provider():
            return

        # How many messages exist past the last summary?
        last = _q1(
            "SELECT summary, messages_through FROM pet_chat_summaries "
            "WHERE pet_id=%s ORDER BY created_at DESC LIMIT 1;",
            (pet_id,),
        )
        floor_id = int(last["messages_through"]) if last and last.get("messages_through") else 0

        count_row = _q1(
            "SELECT COUNT(*) AS n, COALESCE(MAX(id), 0) AS max_id "
            "FROM pet_chat_messages WHERE pet_id=%s AND id > %s;",
            (pet_id, floor_id),
        ) or {}
        n = int(count_row.get("n") or 0)
        if n < SUMMARIZE_EVERY:
            return

        # Summarize everything up to (max_id - SUMMARIZE_KEEP_RECENT most recent)
        # so the user-visible recent turns stay in their original form.
        keep_cutoff_row = _q1(
            "SELECT id FROM pet_chat_messages "
            "WHERE pet_id=%s AND id > %s ORDER BY id DESC "
            "OFFSET %s LIMIT 1;",
            (pet_id, floor_id, SUMMARIZE_KEEP_RECENT),
        )
        if not keep_cutoff_row:
            return  # not enough old messages to fold up
        cutoff_id = int(keep_cutoff_row["id"])

        to_fold = _q(
            "SELECT id, role, content FROM pet_chat_messages "
            "WHERE pet_id=%s AND id > %s AND id <= %s ORDER BY id ASC;",
            (pet_id, floor_id, cutoff_id),
        ) or []
        if not to_fold:
            return

        transcript = "\n".join(
            f"[{m['role']}] {m['content']}" for m in to_fold
        )
        prior_summary = last.get("summary") if last else None

        name = (pet_row.get("name") if pet_row else None) or "the pet"
        prompt_intro = SUMMARY_PROMPT.format(name=name)
        user_content = ""
        if prior_summary:
            user_content += "PRIOR SUMMARY:\n" + prior_summary + "\n\nNEW TRANSCRIPT:\n"
        user_content += transcript

        summary = generate_summary(prompt_intro, user_content)
        if not summary:
            return

        _q(
            "INSERT INTO pet_chat_summaries (pet_id, summary, messages_through) "
            "VALUES (%s, %s, %s);",
            (pet_id, summary, cutoff_id),
            fetch=False,
        )
        log.info("[pets] summarized pet_id=%s through msg_id=%s", pet_id, cutoff_id)
    except Exception as e:
        # Never fail the request on summarizer issues.
        log.warning("[pets] summarize failed: %s", e)


# ---------------------- route registration ----------------------

def register_pets_routes(app, q, q1, login_required, get_db):
    global _q, _q1, _get_db, _login_required
    _q = q
    _q1 = q1
    _get_db = get_db
    _login_required = login_required

    try:
        ensure_pets_schema()
    except Exception as e:
        app.logger.warning(f"[pets_routes] ensure_pets_schema failed: {e}")

    # ---- CRUD ----

    @app.route("/api/pets", methods=["GET"])
    @login_required
    def api_list_pets():
        rows = _q(
            "SELECT * FROM pets WHERE user_id=%s AND is_active=TRUE "
            "ORDER BY created_at ASC;",
            (session["user_id"],),
        ) or []
        return jsonify({"pets": [_dict_pet(r) for r in rows]})

    @app.route("/api/pets", methods=["POST"])
    @login_required
    def api_create_pet():
        data = request.get_json(silent=True) or {}
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "Pet name is required"}), 400

        row = _q1("""
            INSERT INTO pets
              (user_id, name, species, breed, sex, birth_date, weight_lbs,
               color, photo_url, allergies, conditions, medications, notes)
            VALUES
              (%s, %s, %s, %s, %s, %s, %s, %s, %s,
               %s::jsonb, %s::jsonb, %s::jsonb, %s)
            RETURNING *;
        """, (
            session["user_id"],
            name,
            _normalize_species(data.get("species")),
            data.get("breed"),
            _normalize_sex(data.get("sex")),
            data.get("birth_date") or None,
            data.get("weight_lbs"),
            data.get("color"),
            data.get("photo_url"),
            _jsonable(data.get("allergies")),
            _jsonable(data.get("conditions")),
            _jsonable(data.get("medications")),
            data.get("notes") or "",
        ))
        return jsonify({"pet": _dict_pet(row)}), 201

    @app.route("/api/pets/<int:pet_id>", methods=["GET"])
    @login_required
    def api_get_pet(pet_id):
        row = _pet_row_for_user(pet_id, session["user_id"])
        if not row:
            return jsonify({"error": "Not found"}), 404
        return jsonify({"pet": _dict_pet(row)})

    @app.route("/api/pets/<int:pet_id>", methods=["PATCH"])
    @login_required
    def api_update_pet(pet_id):
        row = _pet_row_for_user(pet_id, session["user_id"])
        if not row:
            return jsonify({"error": "Not found"}), 404

        data = request.get_json(silent=True) or {}
        updates = []
        values = []
        for field in _PATCHABLE_FIELDS:
            if field not in data:
                continue
            v = data[field]
            if field in ("allergies", "conditions", "medications"):
                updates.append(f"{field} = %s::jsonb")
                values.append(_jsonable(v))
            elif field == "species":
                updates.append("species = %s")
                values.append(_normalize_species(v))
            elif field == "sex":
                updates.append("sex = %s")
                values.append(_normalize_sex(v))
            else:
                updates.append(f"{field} = %s")
                values.append(v)

        if not updates:
            return jsonify({"pet": _dict_pet(row)})

        updates.append("updated_at = NOW()")
        values.extend([pet_id, session["user_id"]])
        sql = (
            f"UPDATE pets SET {', '.join(updates)} "
            "WHERE id=%s AND user_id=%s RETURNING *;"
        )
        new_row = _q1(sql, tuple(values))
        return jsonify({"pet": _dict_pet(new_row)})

    @app.route("/api/pets/<int:pet_id>", methods=["DELETE"])
    @login_required
    def api_delete_pet(pet_id):
        row = _pet_row_for_user(pet_id, session["user_id"])
        if not row:
            return jsonify({"error": "Not found"}), 404
        _q(
            "UPDATE pets SET is_active=FALSE, updated_at=NOW() "
            "WHERE id=%s AND user_id=%s;",
            (pet_id, session["user_id"]),
            fetch=False,
        )
        return jsonify({"ok": True})

    # ---- Chat (pet-scoped) ----

    @app.route("/api/pets/<int:pet_id>/chat", methods=["GET"])
    @login_required
    def api_pet_chat_history(pet_id):
        row = _pet_row_for_user(pet_id, session["user_id"])
        if not row:
            return jsonify({"error": "Not found"}), 404
        try:
            limit = int(request.args.get("limit", 50))
        except ValueError:
            limit = 50
        limit = max(1, min(200, limit))
        rows = _q(
            "SELECT id, role, content, created_at FROM pet_chat_messages "
            "WHERE pet_id=%s ORDER BY id DESC LIMIT %s;",
            (pet_id, limit),
        ) or []
        msgs = list(reversed(rows))
        return jsonify({"messages": [
            {
                "id": m["id"],
                "role": m["role"],
                "content": m["content"],
                "created_at": m["created_at"].isoformat() if m.get("created_at") else None,
            } for m in msgs
        ]})

    @app.route("/api/pets/<int:pet_id>/chat", methods=["POST"])
    @login_required
    def api_pet_chat_send(pet_id):
        row = _pet_row_for_user(pet_id, session["user_id"])
        if not row:
            return jsonify({"error": "Not found"}), 404

        data = request.get_json(silent=True) or {}
        user_msg = (data.get("message") or "").strip()
        if not user_msg:
            return jsonify({"error": "Empty message"}), 400

        # 1. Persist the user message
        _q(
            "INSERT INTO pet_chat_messages (pet_id, user_id, role, content) "
            "VALUES (%s, %s, 'user', %s);",
            (pet_id, session["user_id"], user_msg),
            fetch=False,
        )

        # 2. Call the LLM (or degrade to a friendly fallback if no provider)
        reply = _llm_reply(pet_id, session["user_id"], row, user_msg)

        # 3. Persist the assistant reply
        _q(
            "INSERT INTO pet_chat_messages (pet_id, user_id, role, content) "
            "VALUES (%s, %s, 'assistant', %s);",
            (pet_id, session["user_id"], reply),
            fetch=False,
        )

        # 4. Best-effort: roll older messages into a summary. Never raises.
        _maybe_summarize_pet_chat(pet_id, session["user_id"], row)

        return jsonify({"reply": reply})
