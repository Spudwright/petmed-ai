"""crittr.ai — Thin LLM client for chat generation and summarization.

Anthropic-first (preferred), OpenAI fallback if only OPENAI_API_KEY is set.

Env vars:
    ANTHROPIC_API_KEY      — preferred provider
    OPENAI_API_KEY         — fallback provider
    ANTHROPIC_MODEL        — default: claude-haiku-4-5-20251001
    OPENAI_MODEL           — default: gpt-4o-mini
    LLM_MAX_TOKENS         — default: 1024
    LLM_RETRIES            — default: 1
    LLM_RETRY_BASE_MS      — default: 250

Public functions:
    generate_chat_reply(system_prompt, history, user_message) -> str
    generate_summary(system_prompt, user_content) -> str
    has_provider() -> bool
    set_fallback_observer(fn) -> None
"""
import os
import time
import logging

log = logging.getLogger("crittr.llm")

ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
try:
    MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "1024"))
except ValueError:
    MAX_TOKENS = 1024
try:
    LLM_RETRIES = int(os.environ.get("LLM_RETRIES", "1"))
except ValueError:
    LLM_RETRIES = 1
try:
    LLM_RETRY_BASE_MS = int(os.environ.get("LLM_RETRY_BASE_MS", "250"))
except ValueError:
    LLM_RETRY_BASE_MS = 250

_anthropic_client = None
_openai_client = None
_fallback_observer = None


def set_fallback_observer(fn):
    """Register fn(provider_failed, stage, err_str) invoked on fallback events."""
    global _fallback_observer
    _fallback_observer = fn


# ── spend governor ───────────────────────────────────────────────────────────
# Wired here rather than at each call site because THIS is the chokepoint: every model
# call in crittr goes through this module, so a cap enforced here cannot be bypassed by
# some future caller forgetting to ask. Both hooks are optional — with neither set the
# module behaves exactly as it did before.
_budget_check = None      # () -> (allowed: bool, reason: str)
_usage_recorder = None    # (provider, model, purpose, prompt_tokens, output_tokens) -> None


def set_budget_hooks(check=None, recorder=None):
    global _budget_check, _usage_recorder
    _budget_check, _usage_recorder = check, recorder


class BudgetExhausted(RuntimeError):
    """Raised instead of calling a provider when the daily cap is spent."""


def _guard(purpose):
    if _budget_check is None:
        return
    try:
        ok, why = _budget_check()
    except Exception as e:                                  # noqa: BLE001
        log.warning("budget check failed, allowing the call: %s", e)
        return
    if not ok:
        log.error("budget: refusing %s call — %s", purpose, why)
        raise BudgetExhausted(why)


def _meter(provider, model, purpose, resp):
    """Record what a completed call actually used. Never raises."""
    if _usage_recorder is None:
        return
    try:
        u = getattr(resp, "usage", None)
        pin = getattr(u, "input_tokens", None)
        if pin is None:
            pin = getattr(u, "prompt_tokens", 0) or 0
        pout = getattr(u, "output_tokens", None)
        if pout is None:
            pout = getattr(u, "completion_tokens", 0) or 0
        _usage_recorder(provider, model, purpose, int(pin or 0), int(pout or 0))
    except Exception as e:                                  # noqa: BLE001
        log.warning("usage recorder failed: %s", e)


def _notify_fallback(provider_failed, stage, err):
    if _fallback_observer is None:
        return
    try:
        _fallback_observer(provider_failed, stage, str(err)[:300])
    except Exception as e:
        log.warning("fallback observer raised: %s", e)


def _call_with_retry(fn, label="llm"):
    """Call fn() with LLM_RETRIES extra retries. Empty replies treated as retryable."""
    last = None
    for attempt in range(LLM_RETRIES + 1):
        try:
            out = fn()
            if out and out.strip():
                return out
            last = ValueError("empty reply from provider")
        except BudgetExhausted:
            # Not a transient failure. Retrying, or falling over to the other provider,
            # would be spending money we have just decided not to spend.
            raise
        except Exception as e:
            last = e
            log.warning("%s attempt %d failed: %s", label, attempt, e)
        if attempt < LLM_RETRIES:
            delay = (LLM_RETRY_BASE_MS / 1000.0) * (2 ** attempt)
            time.sleep(delay)
    raise last if last else RuntimeError("unknown llm failure")


def _get_anthropic():
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
    return bool(_get_anthropic() or _get_openai())


def _extract_anthropic_text(resp):
    parts = []
    for block in getattr(resp, "content", []) or []:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", "") or "")
    return "".join(parts).strip()


def generate_chat_reply(system_prompt, history, user_message):
    """Generate an assistant reply. Anthropic-first; OpenAI on fallback."""
    history = history or []
    messages = list(history) + [{"role": "user", "content": user_message}]

    def _anthropic_call():
        c = _get_anthropic()
        if c is None:
            raise RuntimeError("anthropic unavailable")
        _guard("chat")
        resp = c.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=MAX_TOKENS,
            system=system_prompt,
            messages=messages,
        )
        _meter("anthropic", ANTHROPIC_MODEL, "chat", resp)
        return _extract_anthropic_text(resp)

    def _openai_call():
        c = _get_openai()
        if c is None:
            raise RuntimeError("openai unavailable")
        _guard("chat")
        all_msgs = [{"role": "system", "content": system_prompt}] + messages
        resp = c.chat.completions.create(
            model=OPENAI_MODEL,
            messages=all_msgs,
            max_tokens=MAX_TOKENS,
        )
        _meter("openai", OPENAI_MODEL, "chat", resp)
        return (resp.choices[0].message.content or "").strip()

    if _get_anthropic() is not None:
        try:
            text = _call_with_retry(_anthropic_call, label="chat/anthropic")
            return text or "…"
        except BudgetExhausted:
            raise
        except Exception as e:
            log.warning("anthropic chat failed after retries; falling over: %s", e)
            _notify_fallback("anthropic", "chat", e)

    if _get_openai() is not None:
        try:
            text = _call_with_retry(_openai_call, label="chat/openai")
            return text or "…"
        except Exception as e:
            log.warning("openai chat failed after retries: %s", e)
            _notify_fallback("openai", "chat", e)
            raise

    raise RuntimeError(
        "No LLM provider configured — set ANTHROPIC_API_KEY or OPENAI_API_KEY."
    )


def generate_summary(system_prompt, user_content):
    """Single-turn summarization. Anthropic-first; OpenAI on fallback."""
    def _anthropic_call():
        c = _get_anthropic()
        if c is None:
            raise RuntimeError("anthropic unavailable")
        _guard("summary")
        resp = c.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=MAX_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )
        _meter("anthropic", ANTHROPIC_MODEL, "summary", resp)
        return _extract_anthropic_text(resp)

    def _openai_call():
        c = _get_openai()
        if c is None:
            raise RuntimeError("openai unavailable")
        _guard("summary")
        resp = c.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            max_tokens=MAX_TOKENS,
        )
        _meter("openai", OPENAI_MODEL, "summary", resp)
        return (resp.choices[0].message.content or "").strip()

    if _get_anthropic() is not None:
        try:
            return _call_with_retry(_anthropic_call, label="summary/anthropic")
        except BudgetExhausted:
            raise
        except Exception as e:
            log.warning("anthropic summary failed; falling over: %s", e)
            _notify_fallback("anthropic", "summary", e)

    if _get_openai() is not None:
        try:
            return _call_with_retry(_openai_call, label="summary/openai")
        except Exception as e:
            log.warning("openai summary failed: %s", e)
            _notify_fallback("openai", "summary", e)
            raise

    raise RuntimeError(
        "No LLM provider configured — set ANTHROPIC_API_KEY or OPENAI_API_KEY."
    )
