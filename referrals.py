"""crittr.ai — Referral program (Phase 7.5).

What it does
------------
  * Every active user owns a referral code (short, human-legible, stable).
  * New signups that arrive via `?ref=CODE` or `/r/CODE` redeem it on
    account creation: skip the first-profile fee, and the referrer
    earns a small credit.
  * Credits are a simple ledger; fulfillment (comped paid plan,
    discount codes, etc.) is applied at billing time.

Schemas (auto-created)
----------------------
  referral_codes     owner_user_id -> code
  referral_redemptions  code + new_user_id + ts (unique new_user_id)
  user_credits       user_id, amount_cents, reason, ts

Public API
----------
  register_referral_routes(app, q) -> None
      Wires:
        GET  /r/<code>                 → redirect to /signup?ref=CODE
        GET  /api/referrals/me         → the current user's code + stats
        POST /api/referrals/redeem     → called from signup flow with {code}
  ensure_referral_code(q, user_id) -> str
      Idempotent — creates one if missing.
  redeem_referral(q, code, new_user_id) -> bool
      Best-effort; returns True if applied.
"""
import os
import random
import string
import logging
from flask import request, jsonify, redirect, url_for

log = logging.getLogger("crittr.referrals")


# ---------------------------------------------------------------
# Config
# ---------------------------------------------------------------
try:
    REFERRER_CREDIT_CENTS = int(os.environ.get("REFERRER_CREDIT_CENTS", "500"))
except ValueError:
    REFERRER_CREDIT_CENTS = 500  # $5.00
try:
    REFEREE_CREDIT_CENTS = int(os.environ.get("REFEREE_CREDIT_CENTS", "500"))
except ValueError:
    REFEREE_CREDIT_CENTS = 500

_CODE_ALPHABET = string.ascii_uppercase + string.digits
_CODE_LEN = 6


def _gen_code():
    # Avoid 0/O/1/I confusion.
    confusing = set("0O1I")
    alphabet = "".join(c for c in _CODE_ALPHABET if c not in confusing)
    return "".join(random.choice(alphabet) for _ in range(_CODE_LEN))


# ---------------------------------------------------------------
# Schema
# ---------------------------------------------------------------
def _ensure_schema(q):
    try:
        q(
            """
            CREATE TABLE IF NOT EXISTS referral_codes (
              owner_user_id BIGINT PRIMARY KEY,
              code          TEXT UNIQUE NOT NULL,
              created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS referral_redemptions (
              id            BIGSERIAL PRIMARY KEY,
              code          TEXT NOT NULL,
              new_user_id   BIGINT UNIQUE NOT NULL,
              referrer_id   BIGINT,
              created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS ix_ref_redeem_code
              ON referral_redemptions(code);
            CREATE TABLE IF NOT EXISTS user_credits (
              id            BIGSERIAL PRIMARY KEY,
              user_id       BIGINT NOT NULL,
              amount_cents  INT NOT NULL,
              reason        TEXT,
              created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS ix_user_credits_user
              ON user_credits(user_id, created_at DESC);
            """,
            fetch=False,
        )
    except Exception as e:
        log.warning("[referrals] schema ensure failed: %s", e)


# ---------------------------------------------------------------
# Code issuance & redemption
# ---------------------------------------------------------------
def ensure_referral_code(q, user_id):
    """Return the user's referral code, creating one if absent."""
    try:
        row = q(
            "SELECT code FROM referral_codes WHERE owner_user_id = %s;",
            (user_id,),
        )
        if row:
            return row[0]["code"]
    except Exception as e:
        log.warning("[referrals] lookup failed: %s", e)

    # Retry a few times if we collide
    for _ in range(10):
        code = _gen_code()
        try:
            q(
                "INSERT INTO referral_codes (owner_user_id, code) "
                "VALUES (%s, %s);",
                (user_id, code),
                fetch=False,
            )
            return code
        except Exception as e:
            log.info("[referrals] collision / insert retry: %s", e)
    log.warning("[referrals] could not issue code for user %s", user_id)
    return None


def redeem_referral(q, code, new_user_id):
    """Apply a referral code to a new signup. Returns True on success."""
    if not code:
        return False
    code = code.strip().upper()
    try:
        rows = q(
            "SELECT owner_user_id FROM referral_codes WHERE code = %s;",
            (code,),
        )
    except Exception as e:
        log.warning("[referrals] code lookup failed: %s", e)
        return False
    if not rows:
        return False
    referrer_id = rows[0]["owner_user_id"]
    if referrer_id == new_user_id:
        return False  # self-referral guard

    try:
        # Idempotency: unique(new_user_id) prevents double-redeems.
        q(
            "INSERT INTO referral_redemptions "
            "  (code, new_user_id, referrer_id) "
            "VALUES (%s, %s, %s) "
            "ON CONFLICT (new_user_id) DO NOTHING;",
            (code, new_user_id, referrer_id),
            fetch=False,
        )
        # Credits — best effort, both sides
        q(
            "INSERT INTO user_credits (user_id, amount_cents, reason) "
            "VALUES (%s, %s, %s), (%s, %s, %s);",
            (referrer_id, REFERRER_CREDIT_CENTS, f"referrer_bonus:{code}",
             new_user_id, REFEREE_CREDIT_CENTS, f"referee_bonus:{code}"),
            fetch=False,
        )
        return True
    except Exception as e:
        log.warning("[referrals] redeem insert failed: %s", e)
        return False


# ---------------------------------------------------------------
# Credit ledger helpers
# ---------------------------------------------------------------
def get_credit_balance(q, user_id):
    """Net credit balance in cents for a user (>=0)."""
    try:
        row = q(
            "SELECT COALESCE(SUM(amount_cents), 0) AS bal FROM user_credits WHERE user_id = %s;",
            (user_id,),
        )
        if row:
            return max(0, int(row[0]["bal"] or 0))
    except Exception as e:
        log.warning("[referrals] get_credit_balance failed: %s", e)
    return 0


def record_credit_debit(q, user_id, amount_cents, reason):
    """Insert a negative ledger entry; returns True on success."""
    if amount_cents <= 0:
        return False
    try:
        q(
            "INSERT INTO user_credits (user_id, amount_cents, reason) VALUES (%s, %s, %s);",
            (user_id, -abs(int(amount_cents)), reason),
            fetch=False,
        )
        return True
    except Exception as e:
        log.warning("[referrals] record_credit_debit failed: %s", e)
        return False


def record_credit_reversal(q, user_id, amount_cents, reason):
    """Insert a positive entry to reverse a prior debit."""
    if amount_cents <= 0:
        return False
    try:
        q(
            "INSERT INTO user_credits (user_id, amount_cents, reason) VALUES (%s, %s, %s);",
            (user_id, abs(int(amount_cents)), reason),
            fetch=False,
        )
        return True
    except Exception as e:
        log.warning("[referrals] record_credit_reversal failed: %s", e)
        return False


# ---------------------------------------------------------------
# Stats
# ---------------------------------------------------------------
def _stats(q, user_id):
    try:
        row = q(
            """
            SELECT
              (SELECT COUNT(*) FROM referral_redemptions r
                 WHERE r.referrer_id = %s) AS referrals,
              COALESCE((SELECT SUM(amount_cents) FROM user_credits
                 WHERE user_id = %s), 0) AS credit_cents;
            """,
            (user_id, user_id),
        )
        if row:
            return {
                "referrals": int(row[0]["referrals"] or 0),
                "credit_cents": int(row[0]["credit_cents"] or 0),
            }
    except Exception as e:
        log.warning("[referrals] stats failed: %s", e)
    return {"referrals": 0, "credit_cents": 0}


# ---------------------------------------------------------------
# Routes
# ---------------------------------------------------------------
def register_referral_routes(app, q, require_login=None):
    """Wire referral routes.

    Args:
        app: Flask app.
        q: query helper.
        require_login: optional decorator — apply to endpoints that
          need the current user. If None, we rely on flask.session.
    """
    _ensure_schema(q)

    def _current_user_id():
        try:
            from flask import session
            return session.get("user_id")
        except Exception:
            return None

    @app.route("/r/<code>")
    def referral_landing(code):
        # Sanitize: cap length, alnum only.
        code = (code or "").strip().upper()[:20]
        if not code.isalnum():
            return redirect("/")
        # Store in cookie for signup to pick up.
        resp = redirect(f"/signup?ref={code}")
        resp.set_cookie("crittr_ref", code, max_age=60 * 60 * 24 * 30,
                        httponly=False, samesite="Lax")
        return resp

    @app.route("/api/referrals/me", methods=["GET"])
    def api_referrals_me():
        uid = _current_user_id()
        if not uid:
            return jsonify({"error": "not authenticated"}), 401
        code = ensure_referral_code(q, uid)
        stats = _stats(q, uid)
        base = os.environ.get("BASE_URL", "https://crittr.ai")
        return jsonify({
            "code": code,
            "share_url": f"{base}/r/{code}" if code else None,
            "referrer_credit_cents": REFERRER_CREDIT_CENTS,
            "referee_credit_cents": REFEREE_CREDIT_CENTS,
            **stats,
        })

    @app.route("/api/referrals/redeem", methods=["POST"])
    def api_referrals_redeem():
        """Called from the signup-complete flow with {code}.
        The signup route should have just created the user and set
        session['user_id']."""
        uid = _current_user_id()
        if not uid:
            return jsonify({"error": "not authenticated"}), 401
        data = request.get_json(silent=True) or {}
        code = (data.get("code") or "").strip().upper()
        ok = redeem_referral(q, code, uid)
        return jsonify({"applied": bool(ok), "code": code})
