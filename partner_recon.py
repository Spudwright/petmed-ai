"""crittr.ai — Partner revenue reconciliation (Phase 6.3).

Purpose
-------
Each partner (Vetster, AirVet, Chewy) publishes a weekly affiliate
statement. This script reconciles that statement against our
`partner_clicks` log so you know:

  * How many of our clicks the partner recorded
  * How many converted to paid events
  * How many we logged that they've never confirmed (missed attribution?)
  * Revenue totals

Usage
-----
    python partner_recon.py --partner vetster --statement vetster_2026w17.csv
    python partner_recon.py --partner chewy   --statement chewy_2026_04.csv
    python partner_recon.py --partner vetster --statement vs.csv --json recon.json

Statement formats
-----------------
Expected CSVs share a minimal schema via per-partner column mappings.
If a partner ships a different format, register a new mapping below.

The script is intentionally tolerant: extra columns are ignored;
case-insensitive column matching; empty or missing fields are
treated as "not converted".

Public API
----------
    reconcile(partner_slug, statement_rows, click_rows) -> dict
    load_statement(path, partner_slug) -> list[dict]
    load_clicks(q, partner_slug, since, until) -> list[dict]
"""
import os
import sys
import csv
import json
import argparse
import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger("crittr.partner_recon")

# ---------------------------------------------------------------
# Column mappings per partner
# ---------------------------------------------------------------
# Each mapping says: from a dict-row of the statement CSV, how do we
# extract a normalized record?
_PARTNER_MAPS = {
    "vetster": {
        # Vetster's current CSV (as of 2026 Q1):
        #   Date,Click ID,Customer Status,Booking Amount,Commission
        "ref_token": ["click id", "click_id", "clickid"],
        "status":    ["customer status", "status"],
        "amount":    ["booking amount", "order value", "amount"],
        "commission":["commission", "commission paid"],
        "date":      ["date", "click date"],
    },
    "airvet": {
        "ref_token": ["referral id", "ref", "referral"],
        "status":    ["state", "status"],
        "amount":    ["consult fee", "amount"],
        "commission":["payout", "commission"],
        "date":      ["date"],
    },
    "chewy": {
        "ref_token": ["sub id", "subid", "click id"],
        "status":    ["order status", "status"],
        "amount":    ["order value", "revenue"],
        "commission":["commission"],
        "date":      ["order date", "date"],
    },
}


def _col(row, aliases):
    """Case-insensitive, whitespace-tolerant column lookup."""
    norm = {k.strip().lower(): v for k, v in row.items() if k}
    for a in aliases:
        if a in norm:
            return norm[a]
    return None


def load_statement(path, partner_slug):
    """Read a CSV and return a list of normalized statement rows."""
    mapping = _PARTNER_MAPS.get(partner_slug)
    if not mapping:
        raise ValueError(f"Unknown partner {partner_slug!r}. "
                         f"Known: {list(_PARTNER_MAPS)}")
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            rec = {
                "ref_token":  _col(row, mapping["ref_token"]),
                "status":     _col(row, mapping["status"]),
                "amount":     _col(row, mapping["amount"]),
                "commission": _col(row, mapping["commission"]),
                "date":       _col(row, mapping["date"]),
            }
            # Normalize numerics
            rec["amount"] = _to_num(rec["amount"])
            rec["commission"] = _to_num(rec["commission"])
            out.append(rec)
    return out


def _to_num(v):
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        s = str(v).replace("$", "").replace(",", "").strip()
        return float(s) if s else 0.0
    except Exception:
        return 0.0


# ---------------------------------------------------------------
# Click log loader (from partners.partner_clicks)
# ---------------------------------------------------------------
def load_clicks(q, partner_slug, since=None, until=None):
    """Return partner_clicks rows for the given partner in the window.

    Dates are naive ISO strings; if omitted, defaults to last 14 days.
    """
    if since is None:
        since = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    if until is None:
        until = datetime.now(timezone.utc).isoformat()
    try:
        rows = q(
            "SELECT ref_token, created_at, verdict, user_id, pet_id "
            "FROM partner_clicks "
            "WHERE partner = %s "
            "  AND created_at BETWEEN %s AND %s "
            "ORDER BY created_at ASC;",
            (partner_slug, since, until),
        ) or []
        return rows
    except Exception as e:
        log.warning("load_clicks failed: %s", e)
        return []


# ---------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------
_CONVERTED_STATUSES = {
    "paid", "completed", "confirmed", "approved", "converted",
    "shipped", "delivered",
}
_PENDING_STATUSES = {
    "pending", "pending review", "awaiting", "in review",
}


def reconcile(partner_slug, statement_rows, click_rows):
    """Return a report dict comparing the statement with our logged clicks.

    Fields:
      our_clicks, statement_rows, matched, unmatched_clicks,
      unmatched_statement, converted, pending, revenue_total,
      commission_total.
    """
    our_by_token = {(r.get("ref_token") or "").strip(): r for r in click_rows
                    if r.get("ref_token")}
    stmt_by_token = {(r.get("ref_token") or "").strip(): r for r in statement_rows
                     if r.get("ref_token")}

    matched = []
    unmatched_clicks = []
    unmatched_statement = []
    converted = 0
    pending = 0
    revenue_total = 0.0
    commission_total = 0.0

    for tok, click in our_by_token.items():
        if tok in stmt_by_token:
            stmt = stmt_by_token[tok]
            status = (stmt.get("status") or "").strip().lower()
            matched.append({"ref_token": tok, "status": status,
                            "amount": stmt.get("amount"),
                            "commission": stmt.get("commission")})
            if status in _CONVERTED_STATUSES:
                converted += 1
                revenue_total += float(stmt.get("amount") or 0)
                commission_total += float(stmt.get("commission") or 0)
            elif status in _PENDING_STATUSES:
                pending += 1
        else:
            unmatched_clicks.append(tok)

    for tok in stmt_by_token:
        if tok not in our_by_token:
            unmatched_statement.append(tok)

    our_total = len(our_by_token)
    match_rate = (100 * len(matched) / our_total) if our_total else 0.0
    conv_rate = (100 * converted / len(matched)) if matched else 0.0

    return {
        "partner":               partner_slug,
        "our_clicks":            our_total,
        "statement_rows":        len(stmt_by_token),
        "matched":               len(matched),
        "unmatched_clicks":      len(unmatched_clicks),
        "unmatched_statement":   len(unmatched_statement),
        "converted":             converted,
        "pending":               pending,
        "match_rate_pct":        round(match_rate, 1),
        "conversion_rate_pct":   round(conv_rate, 1),
        "revenue_total":         round(revenue_total, 2),
        "commission_total":      round(commission_total, 2),
        "unmatched_click_tokens": unmatched_clicks[:50],
        "unmatched_statement_tokens": unmatched_statement[:50],
    }


# ---------------------------------------------------------------
# CLI
# ---------------------------------------------------------------
def _load_q_from_app():
    try:
        from app import q  # type: ignore
        return q
    except Exception as e:
        log.warning("could not import q from app: %s", e)
        return None


def _human_report(r):
    lines = [
        f"Partner: {r['partner']}",
        f"  Our clicks:            {r['our_clicks']}",
        f"  Statement rows:        {r['statement_rows']}",
        f"  Matched:               {r['matched']} ({r['match_rate_pct']}%)",
        f"  Unmatched (ours):      {r['unmatched_clicks']}",
        f"  Unmatched (partner's): {r['unmatched_statement']}",
        f"  Converted:             {r['converted']} ({r['conversion_rate_pct']}% of matched)",
        f"  Pending:               {r['pending']}",
        f"  Revenue total:         ${r['revenue_total']:.2f}",
        f"  Commission total:      ${r['commission_total']:.2f}",
    ]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--partner", required=True, choices=sorted(_PARTNER_MAPS))
    ap.add_argument("--statement", required=True, help="CSV path")
    ap.add_argument("--since", help="ISO datetime for click window start")
    ap.add_argument("--until", help="ISO datetime for click window end")
    ap.add_argument("--json", help="Write full report to this JSON path")
    args = ap.parse_args()

    statement = load_statement(args.statement, args.partner)
    q = _load_q_from_app()
    clicks = []
    if q is not None:
        clicks = load_clicks(q, args.partner, since=args.since, until=args.until)
    else:
        print("WARNING: no db connection; running with empty click log.",
              file=sys.stderr)

    report = reconcile(args.partner, statement, clicks)
    print(_human_report(report))
    if args.json:
        with open(args.json, "w") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"\nWrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
