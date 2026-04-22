"""crittr.ai — Thin LLM client for chat generation and summarization.

Anthropic-first (preferred), OpenAI fallback if only OPENAI_API_KEY is set.

Env vars:
    ANTHROPIC_API_KEY      — preferred provider
    OPENAI_API_KEY         — fallback provider
    ANTHROPIC_MODEL        — override default model (default: claude-haiku-4-5-20251001)
    OPENAI_MODEL           — override default model (default: gpt-4o-mini)
    LLM_MAX_TOKENS         — cap assistant reply length (default: 1024)

Public functions:
    generate_chat_reply(system_prompt, history, user_message) -> str
    generate_summary(system_prompt, user_content) -> str
    has_provider() -> bool

Neither function raises on provider errors of the "no API key" kind — it
raises RuntimeError so the caller (pets_routes) can degrade gracefully and
return a friendly message instead of crashing the request.
"""
import os
import logging

log = logging.getLogger("crittr.llm")

# Model defaults (overrideable via env)
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
try:
    MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "1024"))
except ValueError:
    MAX_TOKENS = 1024

_anthropic_client = None
_openai_client = None


def _get_anthropic():
    """Lazy-init Anthropic client. Returns None if unavailable."""
    global _anthropic_client
    if _anthropic_client is not None:
        return _anthropic_client
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        from anthropic import Anthropic
    except Exception as e:
        log.warning("anthropic SDK not installed: %s", e)
        return None
    try:
        _anthropic_client = Anthropic()
    except Exception as e:
        log.warning("anthropic client init failed: %s", e)
        return None
    return _anthropic_client


def _get_openai():
    """Lazy-init OpenAI client. Returns None if unavailable."""
    global _openai_client
    if _openai_client is not None:
        return _openai_client
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    try:
        from openai import OpenAI
    except Exception as e:
        log.warning("openai SDK not installed: %s", e)
        return None
    try:
        _openai_client = OpenAI()
    except Exception as e:
        log.warning("openai client init failed: %s", e)
        return None
    return _openai_client


def has_provider():
    """True iff at least one provider is configured and importable."""
    return bool(_get_anthropic() or _get_openai())


def _extract_anthropic_text(resp):
    parts = []
    for block in getattr(resp, "content", []) or []:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", "") or "")
    return "".join(parts).strip()


def generate_chat_reply(system_prompt, history, user_message):
    """Generate an assistant reply to `user_message` given prior `history`.

    Args:
        system_prompt: str — full system instructions (template filled in).
        history: list of {"role": "user"|"assistant", "content": str},
                 oldest first, NOT including the new user message.
        user_message: str — the user's new message.

    Returns: str (never empty — returns a single "…" if the provider
             gave an empty reply).

    Raises: RuntimeError if no provider is configured.
    """
    history = history or []
    messages = list(history) + [{"role": "user", "content": user_message}]

    c = _get_anthropic()
    if c is not None:
        resp = c.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=MAX_TOKENS,
            system=system_prompt,
            messages=messages,
        )
        text = _extract_anthropic_text(resp)
        return text or "…"

    c = _get_openai()
    if c is not None:
        all_msgs = [{"role": "system", "content": system_prompt}] + messages
        resp = c.chat.completions.create(
            model=OPENAI_MODEL,
            messages=all_msgs,
            max_tokens=MAX_TOKENS,
        )
        text = (resp.choices[0].message.content or "").strip()
        return text or "…"

    raise RuntimeError(
        "No LLM provider configured — set ANTHROPIC_API_KEY or OPENAI_API_KEY."
    )


def generate_summary(system_prompt, user_content):
    """Single-turn summarization. Uses the same providers as generate_chat_reply.

    Raises RuntimeError if no provider is configured.
    """
    c = _get_anthropic()
    if c is not None:
        resp = c.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=MAX_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )
        return _extract_anthropic_text(resp)

    c = _get_openai()
    if c is not None:
        resp = c.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            max_tokens=MAX_TOKENS,
        )
        return (resp.choices[0].message.content or "").strip()

    raise RuntimeError(
        "No LLM provider configured — set ANTHROPIC_API_KEY or OPENAI_API_KEY."
    )

