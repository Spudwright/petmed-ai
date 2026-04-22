"""crittr.ai — Weekly triage QA (Phase 6.7).

What it does
------------
Samples up to N random anon_chats from the last week whose verdict was
ER NOW or VET TOMORROW, re-submits each (owner message + crittr reply)
to a second LLM pass, and asks:
    "Was this verdict correct? Would a vet agree? Any false negatives?"

Writes a small report: JSON + a Markdown summary.

Why: triage rules drift. This gives ongoing, automated evidence that
the rule-set is still holding up — before a bad week of outputs shows
up in customer email.

Usage
-----
    python triage_qa.py                     # sample 20, write reports
    python triage_qa.py --n 50              # sample 50
    python triage_qa.py --report out.md     # custom report path
    python triage_qa.py --dry-run           # don't call the LLM, print plan
"""
import os
import sys
import json
import logging
import argparse
import random
from datetime import datetime

log = logging.getLogger("crittr.triage_qa")


REVIEWER_PROMPT = """You are a senior veterinary triage reviewer. You will be shown
one anonymous pet-health chat: the owner's message and the chatbot's reply
(including its VERDICT line).

Your job: evaluate the reply on three axes.

Respond ONLY with valid JSON. Fields:
  verdict_correct:         "yes" | "no" | "borderline"
  false_negative_risk:     "none" | "low" | "moderate" | "high"
  reasoning:               <2-3 sentence explanation>
  better_verdict:          one of "ER NOW" | "VET TOMORROW" | "SAFE AT HOME"
                           (only if verdict_correct != "yes"; otherwise null)

Criteria:
  * verdict_correct = "no" if a competent vet would disagree with the verdict.
  * false_negative_risk = "high" if the reply looks like it under-triages
    (told someone it was safe when it probably wasn't).
  * Be tough but fair. If the reply is a clarifying question rather than
    a verdict, you may mark verdict_correct="borderline" and note that
    in reasoning.

Example output (this is a format example, not real content):
{"verdict_correct": "yes", "false_negative_risk": "none",
 "reasoning": "Chocolate ingestion at that dose warrants ER.",
 "better_verdict": null}
"""


def _sample_chats(q, days=7, n=20):
    try:
        rows = q(
            """
            SELECT id, created_at, message, reply, verdict
            FROM anon_chats
            WHERE created_at > NOW() - (%s || ' days')::INTERVAL
              AND verdict IN ('ER NOW', 'VET TOMORROW')
              AND reply IS NOT NULL
            ORDER BY random()
            LIMIT %s;
            """,
            (str(days), n),
        ) or []
        return rows
    except Exception as e:
        log.warning("[qa] sample query failed: %s", e)
        return []


def _review_one(msg, reply):
    """Call the LLM reviewer. Returns a dict (possibly with parse_error)."""
    try:
        from llm_client import generate_summary
    except Exception as e:
        return {"parse_error": f"llm_client unavailable: {e}"}

    payload = (
        "OWNER MESSAGE:\n" + (msg or "") + "\n\n"
        "CRITTR REPLY:\n" + (reply or "") + "\n\n"
        "Review this chat per your instructions."
    )
    try:
        raw = generate_summary(REVIEWER_PROMPT, payload)
    except Exception as e:
        return {"parse_error": f"reviewer call failed: {e}"}

    txt = (raw or "").strip()
    # Strip markdown fences if present
    if txt.startswith("```"):
        txt = txt.strip("`")
        # after removing leading ``` language tag, try again
        if "\n" in txt:
            txt = txt.split("\n", 1)[1]
        if txt.endswith("```"):
            txt = txt[:-3]
    try:
        obj = json.loads(txt)
        if not isinstance(obj, dict):
            raise ValueError("reviewer returned non-object")
        return obj
    except Exception as e:
        return {"parse_error": f"json parse: {e}", "raw": raw[:400]}


def run(q, days=7, n=20, dry_run=False):
    rows = _sample_chats(q, days=days, n=n)
    results = []
    for r in rows:
        rec = {
            "id": r.get("id"),
            "created_at": str(r.get("created_at")),
            "verdict": r.get("verdict"),
            "message": (r.get("message") or "")[:600],
            "reply_excerpt": (r.get("reply") or "")[:400],
        }
        if dry_run:
            rec["review"] = {"dry_run": True}
        else:
            rec["review"] = _review_one(r.get("message"), r.get("reply"))
        results.append(rec)
    # Summary stats
    stats = {
        "sampled": len(results),
        "verdict_correct_yes":        0,
        "verdict_correct_no":         0,
        "verdict_correct_borderline": 0,
        "parse_errors":               0,
        "high_fn_risk":               0,
    }
    for rec in results:
        rev = rec.get("review") or {}
        if "parse_error" in rev:
            stats["parse_errors"] += 1
            continue
        vc = (rev.get("verdict_correct") or "").lower()
        if vc == "yes":
            stats["verdict_correct_yes"] += 1
        elif vc == "no":
            stats["verdict_correct_no"] += 1
        elif vc == "borderline":
            stats["verdict_correct_borderline"] += 1
        if (rev.get("false_negative_risk") or "").lower() == "high":
            stats["high_fn_risk"] += 1
    return {"stats": stats, "results": results,
            "generated_at": datetime.utcnow().isoformat() + "Z"}


# ---------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------
def write_markdown(report, path):
    s = report["stats"]
    total = s["sampled"] or 1
    pct_ok = round(100 * s["verdict_correct_yes"] / total, 1)
    lines = [
        "# Triage QA — weekly review",
        "",
        f"_Generated: {report['generated_at']}_",
        "",
        f"Sampled {s['sampled']} ER NOW / VET TOMORROW chats from the last week.",
        "",
        "## Stats",
        "",
        f"- verdict_correct=yes:        **{s['verdict_correct_yes']}** ({pct_ok}%)",
        f"- verdict_correct=no:         **{s['verdict_correct_no']}**",
        f"- verdict_correct=borderline: **{s['verdict_correct_borderline']}**",
        f"- false_negative_risk=high:   **{s['high_fn_risk']}**",
        f"- parse_errors:               **{s['parse_errors']}**",
        "",
        "## Flagged cases (verdict_correct != yes)",
        "",
    ]
    flagged = 0
    for r in report["results"]:
        rev = r.get("review") or {}
        vc = (rev.get("verdict_correct") or "").lower()
        if vc == "yes":
            continue
        flagged += 1
        lines.append(f"### #{r['id']} — {r['verdict']}")
        lines.append(f"- **msg**: {r['message']}")
        lines.append(f"- **verdict_correct**: {rev.get('verdict_correct')}")
        lines.append(f"- **false_negative_risk**: {rev.get('false_negative_risk')}")
        lines.append(f"- **better_verdict**: {rev.get('better_verdict')}")
        lines.append(f"- **reasoning**: {rev.get('reasoning')}")
        lines.append("")
    if not flagged:
        lines.append("_None — all sampled verdicts were confirmed by the reviewer._")
    with open(path, "w") as f:
        f.write("\n".join(lines))


def _load_q_from_app():
    try:
        from app import q  # type: ignore
        return q
    except Exception as e:
        log.warning("could not import q from app: %s", e)
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--report", default="triage_qa_report.md")
    ap.add_argument("--json", default="triage_qa_report.json")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    q = _load_q_from_app()
    if q is None:
        print(json.dumps({"error": "no db handle"}))
        return 2
    report = run(q, days=args.days, n=args.n, dry_run=args.dry_run)
    with open(args.json, "w") as f:
        json.dump(report, f, indent=2, default=str)
    write_markdown(report, args.report)
    print(json.dumps(report["stats"], indent=2))
    print("Wrote:", args.report, "+", args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
