"""crittr.ai — anonymous hero-chat triage endpoint (/api/chat/anon).

This is the core funnel endpoint the frontend hero calls.  It:
  1. Runs the AI triage prompt (returns ER NOW / VET TOMORROW / SAFE AT HOME)
  2. Parses the verdict from the AI reply
  3. Attaches a partner card (Vetster / AirVet) for VET TOMORROW + ER NOW
  4. Attaches AI-picked product recommendations for SAFE AT HOME
  5. Logs the exchange to anon_chats for the admin dashboard

Partner URLs come from env vars (VETSTER_PARTNER_URL, AIRVET_PARTNER_URL)
so you drop in your partner/affiliate IDs on Railway without a code change.

Public API
----------
    register_anon_chat_routes(app, q)
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets

from flask import jsonify, request

log = logging.getLogger("crittr.anon_chat")


_TRIAGE_PROMPT = """You are crittr.ai's AI pet triage assistant. A worried pet owner is describing what's happening with their animal. Your job is to give them a clear three-verdict read: ER NOW, VET TOMORROW, or SAFE AT HOME.

# Output format — STRICT

First, write 1-3 short sentences of calm, warm acknowledgment — no clinical jargon, no lists, just honest reassurance like a knowledgeable friend. Then on new lines, output exactly:

VERDICT: <ER NOW | VET TOMORROW | SAFE AT HOME>
WHY: <one plain-English sentence with the specific reason>
NEXT: <one sentence on what to do in the next 15 minutes (or tonight / this week, matched to the verdict)>

# When to pick each verdict

ER NOW:
- Collapse, seizure, loss of consciousness
- Bloat / distended abdomen (especially deep-chested dogs)
- Labored or rapid breathing, open-mouth breathing in a cat
- Pale or blue gums
- Hit-by-car, major trauma, suspected fracture
- Uncontrolled bleeding
- Known toxin ingestion: chocolate in meaningful amount, xylitol, grapes/raisins, onions/garlic, lilies for cats, human meds (NSAIDs, acetaminophen, antidepressants), rodenticide, antifreeze
- Male cat unable to urinate (urinary blockage — hours matter)
- Severe open wound
- Prolonged unstoppable vomiting/diarrhea (more than a handful of episodes, blood, or weak dog/cat)

VET TOMORROW:
- Limping more than 24 hours without improvement
- Skin infection / hot spot with oozing or raw tissue
- Ear infection (head shaking, smell, discharge)
- Vomiting or diarrhea 1-2x but otherwise acting normal
- Dental pain, drooling, pawing at mouth
- Eye squinting without cloudiness
- Chronic scratching with no fleas found
- Weight loss over weeks
- New behavioral change that's not violent

SAFE AT HOME:
- Tick removal (no sign of engorgement-related illness)
- Minor scrape, small superficial wound
- Single vomit or single soft stool with normal energy and appetite otherwise
- Normal seasonal shedding, normal age-related slowing
- Gas, mild appetite fluctuation for one day

# Rules

- When in doubt, pick the more cautious verdict.
- For cats specifically, lower the bar for ER NOW — cats hide illness; any breathing difficulty or urinary obstruction is emergency.
- Do not list products, dosages, or treatment protocols. Triage only.
- Do not ask clarifying questions in the same reply as the verdict. If the owner's description is too vague for a verdict, ask ONE question and stop — don't emit a VERDICT line at all yet.

# If the owner attaches a photo
- You are VET AI, a triage-trained visual-capable veterinary assistant.
- Describe only clinically-relevant features you can actually see (redness, swelling, discharge, posture, eye appearance, skin lesions, wounds, etc.). Do NOT invent details.
- Map visual findings to the same three-verdict output. A photo of an obvious serious wound, pale gums, labored breathing posture, or clear ER-tier finding should push the verdict toward ER NOW.
- If the image is unclear or doesn't show the concern well, say so in one sentence and ask for a better angle — do not emit a VERDICT line in that case.
"""


_VERDICT_RE = re.compile(r"^\s*VERDICT\s*:\s*(ER NOW|VET TOMORROW|SAFE AT HOME)\s*$", re.I | re.M)


def _parse_verdict(text: str) -> str | None:
    if not text:
        return None
    m = _VERDICT_RE.search(text)
    return m.group(1).upper() if m else None


# ── Partner registry — URLs come from env, with safe defaults ─────────────
def _partner(slug: str, verdict: str) -> dict | None:
    """Return a partner card dict for the given verdict, or None if skip."""
    if verdict not in ("ER NOW", "VET TOMORROW"):
        return None

    # Vetster is our default teletriage partner; AirVet is secondary.
    # Env var format: https://vetster.com/?partner=<YOUR_ID> or full affiliate URL.
    vetster_url = os.environ.get(
        "VETSTER_PARTNER_URL",
        "https://vetster.com/?utm_source=crittr&utm_medium=triage",
    )
    airvet_url = os.environ.get(
        "AIRVET_PARTNER_URL",
        "https://airvet.com/?utm_source=crittr&utm_medium=triage",
    )

    # For ER NOW we deliberately do NOT recommend teletriage — we want
    # them in a physical emergency vet. Return a partner card that points
    # at an ER locator, or just None so the frontend surfaces its
    # 'find a local ER vet' guidance instead.
    if verdict == "ER NOW":
        return {
            "name": "Find an emergency vet near you",
            "url": "/find-vet?tier=er",
            "cta": "Find ER vet",
            "price_hint": "Go now — every minute counts",
            "hours": None,
        }

    # VET TOMORROW → teletriage partner (first pick: Vetster)
    return {
        "name": "Vetster — video consult with a licensed vet",
        "url": vetster_url,
        "cta": "Book a video consult",
        "price_hint": "From $60 · usually same-day",
        "hours": "Mon–Sun, extended hours",
    }


# ── AI-picked product recommendations for SAFE AT HOME verdicts ────────────
def _picks_for_safe(q, user_message: str) -> list[dict]:
    """Return up to 3 OTC products relevant to the described symptoms."""
    try:
        rows = q(
            "SELECT slug, public_name, public_blurb, price_cents, amazon_url, "
            "       species, tags, description, image_url "
            "FROM products "
            "WHERE in_stock = TRUE AND requires_rx = FALSE "
            "      AND amazon_url IS NOT NULL AND amazon_url <> ''"
        ) or []
    except Exception as e:
        log.warning("picks query failed: %s", e)
        return []

    if not rows:
        return []

    msg = (user_message or "").lower()
    # Simple keyword map → category hint. Keep this dumb; LLM-driven
    # recommendations happen via AI top picks route later.
    keyword_map = [
        (("itch", "scratch", "allergy", "hot spot", "coat", "shedding"),
         ("omega", "skin", "coat")),
        (("flea", "tick"),
         ("flea", "tick", "collar", "topical")),
        (("joint", "limp", "stiff", "hip", "arthritis", "old", "senior", "stair"),
         ("joint", "mobility")),
        (("anxiety", "scared", "afraid", "storm", "firework", "separation", "vet visit", "new home", "barking"),
         ("calm", "pheromone", "behavior")),
        (("gut", "tummy", "vomit", "diarrhea", "loose stool", "probiotic"),
         ("probiotic", "gut", "digestive")),
        (("teeth", "breath", "dental", "plaque"),
         ("dental",)),
        (("vitamin", "nutrient", "picky eater"),
         ("multivitamin", "vitamin")),
    ]

    def score(row) -> int:
        s = 0
        text = " ".join(
            str(row.get(k) or "").lower()
            for k in ("public_name", "public_blurb", "description", "tags")
        )
        for triggers, needles in keyword_map:
            if any(t in msg for t in triggers):
                for n in needles:
                    if n in text:
                        s += 2
        return s

    scored = [(score(r), r) for r in rows]
    scored.sort(key=lambda t: -t[0])
    picked = [r for (s, r) in scored if s > 0][:3]
    # Fallback: if nothing scored, return 2 popular OTC (first rows).
    if not picked:
        picked = rows[:2]

    out = []
    for p in picked:
        out.append({
            "name":       p.get("public_name") or p.get("description", "Recommended product")[:60],
            "reason":     p.get("public_blurb") or "",
            "price_hint": f"${(p.get('price_cents') or 0) / 100:.0f}+",
            "brand":      "",  # intentionally blank — we don't surface manufacturer brands
            "url":        p.get("amazon_url"),
        })
    return out


def _ensure_anon_chat_schema(q):
    try:
        q(
            """CREATE TABLE IF NOT EXISTS anon_chats (
                 id BIGSERIAL PRIMARY KEY,
                 session_id TEXT,
                 owner_message TEXT,
                 assistant_reply TEXT,
                 verdict TEXT,
                 created_at TIMESTAMPTZ DEFAULT NOW()
               )""",
            fetch=False,
        )
        q(
            "CREATE INDEX IF NOT EXISTS anon_chats_created_idx ON anon_chats (created_at DESC)",
            fetch=False,
        )
    except Exception as e:
        log.warning("ensure_anon_chat_schema: %s", e)


def register_anon_chat_routes(app, q, ai_chat):
    """Wire POST /api/chat/anon into the Flask app.

    Parameters
    ----------
    app : Flask
    q   : the shared query helper from app.py
    ai_chat : the ai_chat() function from app.py (inject to avoid circular imports)
    """
    _ensure_anon_chat_schema(q)

    @app.route("/api/chat/anon", methods=["POST"])
    def api_chat_anon():
        d = request.json or {}
        message = (d.get("message") or "").strip()
        history = d.get("history") or []
        sid = (d.get("session_id") or secrets.token_hex(8))[:32]

        image_data_url = (d.get("image_base64") or "").strip()
        if not message and not image_data_url:
            return jsonify({"error": "Message or image required"}), 400

        # Basic image validation — must be data: URL with an image mime type.
        # Limit: ~6MB base64 (roughly 4.5MB raw) to avoid model token explosions.
        if image_data_url:
            if not image_data_url.startswith("data:image/"):
                return jsonify({"error": "image_base64 must be a data:image/... URL"}), 400
            if len(image_data_url) > 7_500_000:
                return jsonify({"error": "image too large; please resize to under 4MB"}), 413

        # Build the conversation, capping history to avoid runaway context.
        messages = []
        for h in history[-10:]:
            role = h.get("role", "user")
            content = (h.get("content") or "")[:2000]
            if content:
                messages.append({"role": role, "content": content})

        # Compose the final user turn — text + optional image for gpt-4o-mini vision.
        text_for_model = message[:2000] if message else "Here's a photo of my pet. Please tell me what you see."
        if image_data_url:
            user_content = [
                {"type": "text", "text": text_for_model},
                {"type": "image_url", "image_url": {"url": image_data_url, "detail": "low"}},
            ]
        else:
            user_content = text_for_model
        messages.append({"role": "user", "content": user_content})

        # Call the AI with the triage-specific system prompt (gpt-4o-mini default
        # accepts multi-modal content out of the box).
        reply = ai_chat(messages, system_prompt=_TRIAGE_PROMPT, tier="default")
        verdict = _parse_verdict(reply)

        partner = None
        picks = []
        if verdict in ("ER NOW", "VET TOMORROW"):
            partner = _partner("vetster", verdict)
        elif verdict == "SAFE AT HOME":
            picks = _picks_for_safe(q, message)

        # Log (best-effort — never block the response on logging)
        try:
            q(
                "INSERT INTO anon_chats (session_id, owner_message, assistant_reply, verdict) "
                "VALUES (%s, %s, %s, %s)",
                (sid, (message or "(image)")[:4000], (reply or "")[:8000], verdict),
                fetch=False,
            )
        except Exception as e:
            log.debug("anon_chats insert failed: %s", e)

        return jsonify({
            "reply":        reply,
            "verdict":      verdict,
            "partner":      partner,
            "picks":        picks,
            "session_id":   sid,
            "suggest_signup": verdict == "SAFE AT HOME" and len(history) >= 2,
        })
