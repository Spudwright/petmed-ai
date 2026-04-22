"""crittr.ai — Nightly background jobs (Phase 6.6).

Runs once a night (cron). Two jobs today; more will slot in here.

Jobs
----
  1. summarize_stale_pets
     Re-runs the pet-chat summarizer against every active pet whose last
     message is > 24h old. Catches pets whose summaries got stale because
     no new message has arrived to trigger the on-write summarizer.

  2. cap_summary_size (stretch; enforces running-summary length cap)
     Truncates pet_chat_summaries.summary to SUMMARY_MAX_CHARS, keeping
     the tail (most recent facts). Guards against unbounded growth.

Usage
-----
    python nightly_jobs.py                   # run both jobs
    python nightly_jobs.py --only summarize  # run one
    python nightly_jobs.py --dry-run         # log actions, take no writes

Importable:
    from nightly_jobs import run as run_nightly
    run_nightly(q, dry_run=False)
"""
import os
import sys
import json
import logging
import argparse

log = logging.getLogger("crittr.nightly")

try:
    SUMMARY_MAX_CHARS = int(os.environ.get("SUMMARY_MAX_CHARS", "6000"))
except ValueError:
    SUMMARY_MAX_CHARS = 6000


# ---------------------------------------------------------------
# Job 1 — re-summarize quiet pets
# ---------------------------------------------------------------
def _active_stale_pets(q):
    """Pets with a recent message but no summary covering it, OR pets
    whose last summary is > 24h older than their last message."""
    try:
        rows = q(
            """
            WITH last_msg AS (
              SELECT pet_id, MAX(id) AS last_id, MAX(created_at) AS last_ts
              FROM pet_chat_messages
              GROUP BY pet_id
            ),
            last_sum AS (
              SELECT pet_id, MAX(messages_through) AS sum_through,
                     MAX(created_at) AS sum_ts
              FROM pet_chat_summaries
              GROUP BY pet_id
            )
            SELECT p.id AS pet_id, p.user_id, p.name,
                   lm.last_id, lm.last_ts,
                   ls.sum_through, ls.sum_ts
            FROM pets p
            JOIN last_msg lm ON lm.pet_id = p.id
            LEFT JOIN last_sum ls ON ls.pet_id = p.id
            WHERE p.is_active = TRUE
              AND lm.last_ts > NOW() - INTERVAL '30 days'
              AND (
                ls.sum_through IS NULL
                OR ls.sum_through < lm.last_id
                OR ls.sum_ts < lm.last_ts - INTERVAL '24 hours'
              );
            """
        ) or []
        return rows
    except Exception as e:
        log.warning("[nightly] active_stale_pets query failed: %s", e)
        return []


def summarize_stale_pets(q, dry_run=False):
    """Pull stale pets, load the pet row, and invoke the existing
    summarizer helper in pets_routes-v2."""
    try:
        # Lazy import so tests don't need pets_routes loaded.
        from pets_routes_v2 import _maybe_summarize_pet_chat  # type: ignore
    except Exception:
        try:
            # Accept either module name — '-' isn't valid, but some
            # deployments symlink it.
            import importlib
            pr = importlib.import_module("pets_routes-v2")
            _maybe_summarize_pet_chat = getattr(pr, "_maybe_summarize_pet_chat")
        except Exception as e:
            log.warning("[nightly] cannot import summarizer: %s", e)
            return {"ran": 0, "skipped_reason": str(e)}

    rows = _active_stale_pets(q)
    counts = {"candidates": len(rows), "ran": 0, "errors": 0}
    for r in rows:
        pet_id = r["pet_id"]
        user_id = r["user_id"]
        pet_row = dict(r)
        if dry_run:
            log.info("[nightly] would summarize pet=%s user=%s", pet_id, user_id)
            continue
        try:
            _maybe_summarize_pet_chat(pet_id, user_id, pet_row)
            counts["ran"] += 1
        except Exception as e:
            counts["errors"] += 1
            log.warning("[nightly] summarize pet=%s failed: %s", pet_id, e)
    return counts


# ---------------------------------------------------------------
# Job 2 — enforce running-summary size cap
# ---------------------------------------------------------------
def cap_summary_size(q, dry_run=False):
    """Trim any pet_chat_summaries.summary that exceeds SUMMARY_MAX_CHARS,
    keeping the tail (the most recent rollup is richest on recent facts)."""
    try:
        rows = q(
            "SELECT id, LENGTH(summary) AS sz FROM pet_chat_summaries "
            "WHERE LENGTH(summary) > %s;",
            (SUMMARY_MAX_CHARS,),
        ) or []
    except Exception as e:
        log.warning("[nightly] size-cap lookup failed: %s", e)
        return {"checked": 0, "trimmed": 0, "errors": 1}

    counts = {"checked": len(rows), "trimmed": 0, "errors": 0}
    for r in rows:
        if dry_run:
            log.info("[nightly] would trim summary id=%s (%d chars)",
                     r["id"], r["sz"])
            continue
        try:
            q(
                "UPDATE pet_chat_summaries "
                "SET summary = RIGHT(summary, %s) WHERE id = %s;",
                (SUMMARY_MAX_CHARS, r["id"]),
                fetch=False,
            )
            counts["trimmed"] += 1
        except Exception as e:
            counts["errors"] += 1
            log.warning("[nightly] trim id=%s failed: %s", r["id"], e)
    return counts


# ---------------------------------------------------------------
# Runner
# ---------------------------------------------------------------
JOBS = {
    "summarize": summarize_stale_pets,
    "cap_summary": cap_summary_size,
}


def run(q, only=None, dry_run=False):
    results = {}
    for name, fn in JOBS.items():
        if only and name != only:
            continue
        try:
            results[name] = fn(q, dry_run=dry_run)
        except Exception as e:
            log.warning("[nightly] job %s raised: %s", name, e)
            results[name] = {"error": str(e)}
    return results


def _load_q_from_app():
    try:
        from app import q  # type: ignore
        return q
    except Exception as e:
        log.warning("[nightly] could not import q from app: %s", e)
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=sorted(JOBS.keys()))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    q = _load_q_from_app()
    if q is None:
        print(json.dumps({"error": "no db handle (app.q not importable)"}))
        return 2
    out = run(q, only=args.only, dry_run=args.dry_run)
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
