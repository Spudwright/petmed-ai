"""
scheduler.py — embedded APScheduler for Phase 6+7 periodic jobs.

Runs inside the web process so we don't need per-job Railway services.
Loses in-progress jobs on restart; acceptable for these workloads.

Jobs (mirrors PHASE-6-7-SHIPPED.md cron table):
- alerts.py            every 15 min
- nightly_jobs.py      daily 03:00 UTC
- weekly_digest.py     Monday 07:00 UTC
- triage_qa.py         Monday 10:00 UTC
- partner_recon.py     first Monday of month 09:00 UTC (skipped by default)

Env vars:
- SCHEDULER_ENABLED   "1" to start (default "1")
- RUN_PARTNER_RECON   "1" to include partner_recon (default "0" — needs statement file)
"""

import os
import subprocess
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).parent


def _run_script(name: str, *args: str) -> None:
    """Run a sibling python script, logging stdout/stderr."""
    cmd = [sys.executable, str(ROOT / name), *args]
    try:
        print(f"[scheduler] running {name} {' '.join(args)}", flush=True)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.stdout:
            print(f"[scheduler] {name} stdout:\n{r.stdout}", flush=True)
        if r.returncode != 0:
            print(f"[scheduler] {name} FAILED rc={r.returncode} stderr:\n{r.stderr}", flush=True)
    except Exception:
        print(f"[scheduler] {name} EXCEPTION:\n{traceback.format_exc()}", flush=True)


def _run_alerts():          _run_script("alerts.py")
def _run_nightly():         _run_script("nightly_jobs.py")
def _run_weekly_digest():   _run_script("weekly_digest.py")
def _run_triage_qa():       _run_script("triage_qa.py", "--n", "20")
def _run_partner_recon():
    if os.environ.get("RUN_PARTNER_RECON", "0") != "1":
        return
    _run_script("partner_recon.py", "--partner", "vetster",
                "--statement", "/var/crittr/statements/latest.csv")


def start_scheduler() -> "object | None":
    """Start the APScheduler with all Phase 6+7 jobs. Idempotent."""
    if os.environ.get("SCHEDULER_ENABLED", "1") != "1":
        print("[scheduler] disabled via SCHEDULER_ENABLED=0", flush=True)
        return None
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        print("[scheduler] apscheduler not installed — skipping", flush=True)
        return None

    # Fork-safe single-instance guard: only one worker holds the lock.
    # If gunicorn runs multiple workers, the others silently skip.
    import fcntl
    lock_path = "/tmp/crittr-scheduler.lock"
    try:
        _lock_fd = open(lock_path, "w")
        fcntl.flock(_lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError) as _lock_err:
        print(f"[scheduler] another worker owns the lock, skipping: {_lock_err}", flush=True)
        return None
    # Keep the fd alive for the process lifetime
    globals()["_SCHEDULER_LOCK_FD"] = _lock_fd

    sched = BackgroundScheduler(daemon=True, timezone="UTC")

    sched.add_job(_run_alerts,        CronTrigger(minute="*/15"),           id="alerts",        max_instances=1)
    sched.add_job(_run_nightly,       CronTrigger(hour=3, minute=0),        id="nightly",       max_instances=1)
    sched.add_job(_run_weekly_digest, CronTrigger(day_of_week="mon", hour=7, minute=0),  id="weekly_digest", max_instances=1)
    sched.add_job(_run_triage_qa,     CronTrigger(day_of_week="mon", hour=10, minute=0), id="triage_qa",     max_instances=1)
    sched.add_job(_run_partner_recon, CronTrigger(day="1-7", day_of_week="mon", hour=9, minute=0), id="partner_recon", max_instances=1)

    sched.start()
    print(f"[scheduler] started with jobs: {[j.id for j in sched.get_jobs()]}", flush=True)
    return sched
