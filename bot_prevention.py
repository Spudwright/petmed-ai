"""crittr.ai — Phase I.2: bot prevention layer.

Two complementary defenses on top of rate limiting:

1. Honeypot — a hidden form field that real users never fill in but
   most automated scrapers/bots will. If it's populated, reject.
2. Cloudflare Turnstile — invisible CAPTCHA that verifies humanness
   server-side BEFORE the request hits expensive endpoints (OpenAI).

Honeypot works immediately. Turnstile activates when both
TURNSTILE_SITE_KEY (frontend) and TURNSTILE_SECRET_KEY (backend) env
vars are present; without them, only the honeypot runs (graceful
degradation).

Public API
----------
    is_bot_request(request_json)      -> (is_bot, reason)
    verify_turnstile_token(token, ip) -> (ok, reason)
    turnstile_site_key()              -> str | None  (for frontend)
"""
from __future__ import annotations

import logging
import os

import urllib.request
import urllib.parse
import json

log = logging.getLogger("crittr.bot_prevention")


# Hidden field name. Real frontend never populates this. Bots filling
# every <input> they see will trip it.
_HONEYPOT_FIELD = "website"  # named to look attractive to dumb bots

_TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def turnstile_site_key():
    """Return the public site key for the frontend, or None if not configured."""
    return (os.environ.get("TURNSTILE_SITE_KEY") or "").strip() or None


def _turnstile_secret_key():
    return (os.environ.get("TURNSTILE_SECRET_KEY") or "").strip() or None


def is_bot_request(payload: dict) -> tuple[bool, str]:
    """Honeypot check. Cheap, runs first.

    Returns (True, reason) if the request looks like a bot.
    """
    if not isinstance(payload, dict):
        return False, ""
    val = payload.get(_HONEYPOT_FIELD)
    if val is None or val == "":
        return False, ""
    log.info("bot_prevention: honeypot triggered (field=%s, value=%r)",
             _HONEYPOT_FIELD, str(val)[:40])
    return True, "honeypot"


def verify_turnstile_token(token: str, client_ip: str | None = None) -> tuple[bool, str]:
    """Verify a Cloudflare Turnstile token server-side.

    Returns (True, "") if Turnstile says human; (False, reason) otherwise.
    If TURNSTILE_SECRET_KEY is not set, returns (True, "disabled") so
    the endpoint stays usable until the user configures keys.
    """
    secret = _turnstile_secret_key()
    if not secret:
        return True, "disabled"

    if not token:
        return False, "missing_token"

    data = {"secret": secret, "response": token}
    if client_ip:
        data["remoteip"] = client_ip
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(_TURNSTILE_VERIFY_URL, data=body, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            j = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log.warning("turnstile: verify call failed: %s", e)
        # Fail-open on Cloudflare outage so we don't block legitimate users.
        # Rate limiter is still active as a backstop.
        return True, "verify_unreachable"

    if j.get("success"):
        return True, "ok"
    codes = ",".join(j.get("error-codes") or [])
    log.info("turnstile: rejected (codes=%s)", codes)
    return False, f"rejected:{codes}"
