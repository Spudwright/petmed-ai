"""crittr.ai — Phase I.1: per-IP rate limiting for public endpoints.

Wraps Flask-Limiter to throttle abuse of /api/chat/anon and other public
endpoints that hit OpenAI or do expensive work. Real users will never
trigger these caps; bots get 429-blocked immediately.

In-memory storage for simplicity (single Railway worker). When we go
multi-worker, swap to Redis via RATELIMIT_STORAGE_URI env var.

Behind a proxy (Railway, Cloudflare), real client IP comes from the
X-Forwarded-For header. We trust the *first* hop in that header.

Public API
----------
    init_rate_limiter(app)   -> Limiter instance
    chat_limits              -> list[str]  (apply via @limiter.limit(...))
    light_limits             -> list[str]  (less strict, for cheaper endpoints)
"""
from __future__ import annotations

import logging
import os

from flask import jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

log = logging.getLogger("crittr.rate_limit")


# Strict limits for the AI chat endpoint (each call burns OpenAI credit)
# 10/min, 60/hour, 200/day per IP. Real users will never hit these.
chat_limits = ["10 per minute", "60 per hour", "200 per day"]

# Lighter limits for cheaper endpoints (product lookups, metadata, etc.)
light_limits = ["60 per minute", "600 per hour", "2000 per day"]

# Default fallback for any other Flask route (not strict, just a safety net)
default_limits = ["120 per minute", "1500 per hour"]


def _client_ip():
    """Best-effort real client IP behind Railway/Cloudflare proxy.

    Trusts the first hop in X-Forwarded-For (the closest client). Falls
    back to remote_addr.
    """
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return get_remote_address() or "unknown"


def init_rate_limiter(app):
    """Wire Flask-Limiter into the Flask app and return the limiter."""
    storage_uri = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")

    limiter = Limiter(
        key_func=_client_ip,
        app=app,
        default_limits=default_limits,
        storage_uri=storage_uri,
        strategy="fixed-window",
        headers_enabled=True,  # adds X-RateLimit-* response headers
    )

    @app.errorhandler(429)
    def _ratelimit_handler(e):
        log.warning(
            "rate-limit hit: ip=%s path=%s ua=%s",
            _client_ip(),
            request.path,
            (request.headers.get("User-Agent") or "")[:80],
        )
        return (
            jsonify({
                "error": "Too many requests. Please slow down and try again.",
                "retry_after_seconds": getattr(e, "retry_after", 60),
            }),
            429,
        )

    log.info("rate-limit: initialized (storage=%s)", storage_uri.split("://")[0])
    return limiter
