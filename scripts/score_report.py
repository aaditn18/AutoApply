#!/usr/bin/env python3
"""
score_report.py — Show scores and status for every job in the DB.

Usage:
    python scripts/score_report.py                     # all jobs, sorted by rank
    python scripts/score_report.py --status scored     # only scored jobs
    python scripts/score_report.py --status rejected   # rejected_* statuses
    python scripts/score_report.py --status applied    # applied_* statuses
    python scripts/score_report.py --track quant       # filter by track
    python scripts/score_report.py --company stripe    # case-insensitive filter
    python scripts/score_report.py --top 20            # top N by final_rank
    python scripts/score_report.py --json              # machine-readable JSON

Score breakdown (final_rank = base_fit + pay_signal + loc_signal):
    base_fit    0.0–1.0   LLM/placeholder fit score
    pay_signal  0.0–0.30  extracted salary tier bonus
    loc_signal  0.0–0.15  NYC location bonus
    final_rank  0.0–1.45  sorting key
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from autoapply.tracker.db import create_engine_from_settings
from autoapply.tracker.models import Job
from sqlalchemy.orm import Session
from sqlalchemy import or_


# Status groups for the --status convenience flag
_STATUS_GROUPS: dict[str, list[str]] = {
    "new":      ["new"],
    "scored":   ["scored"],
    "applied":  ["applied_ok", "applied_failed", "applied_captcha"],
    "rejected": ["rejected_by_location", "rejected_by_injection",
                 "rejected_by_yoe", "rejected_by_dedup"],
    "review":   ["queued_review"],
}


def _status_icon(status: str) -> str:
    if status == "scored":           return "🟡"
    if status == "applied_ok":       return "✅"
    if status == "applied_failed":   return "❌"
    if status == "applied_captcha":  return "🔒"
    if status == "queued_review":    return "👀"
    if status.startswith("rejected"): return "🚫"
    if status == "new":              return "🆕"
    return "❓"


def _pay_str(midpoint: float | None) -> str:
    if midpoint is None:
        return "—"
    k = int(midpoint / 1000)
    return f"${k}k"


def main() -> None:
    parser = argparse.ArgumentParser(description="Score report for all jobs in the DB.")
    parser.add_argument("--status",  help="Filter: new|scored|applied|rejected|review  or a specific status string")
    parser.add_argument("--track",   help="Filter by track (swe|ml|hpc|quant)")
    parser.add_argument("--company", help="Case-insensitive company substring filter")
    parser.add_argument("--top",     type=int, default=0, help="Show only top N by final_rank")
    parser.add_argument("--json",    action="store_true", help="Output JSON")
    parser.add_argument("--asc",     action="store_true", help="Sort ascending (lowest rank first)")
    args = parser.parse_args()

    engine = create_engine_from_settings()
    with Session(engine) as s:
        q = s.query(Job)

        # Status filter
        if args.status:
            group = _STATUS_GROUPS.get(args.status)
            if group:
                q = q.filter(or_(*[Job.status == st for st in group]))
            else:
                # Exact status or prefix match
                q = q.filter(Job.status.ilike(f"{args.status}%"))

        # Track filter
        if args.track:
            q = q.filter(Job.track == args.track.lower())

        # Company filter
        if args.company:
            q = q.filter(Job.company.ilike(f"%{args.company}%"))

        # Sort
        if args.asc:
            q = q.order_by(Job.final_rank.asc())
        else:
            q = q.order_by(Job.final_rank.desc())

        jobs = q.all()

    if args.top:
        jobs = jobs[:args.top]

    if not jobs:
        print("No jobs match the filter.")
        return

    if args.json:
        out = []
        for j in jobs:
            out.append({
                "id":            j.id,
                "status":        j.status,
                "source":        j.source,
                "company":       j.company,
                "title":         j.title,
                "location":      j.location,
                "track":         j.track,
                "final_rank":    round(j.final_rank or 0.0, 3),
                "base_fit":      round(j.base_fit or 0.5, 3),
                "pay_signal":    round(j.pay_signal or 0.0, 3),
                "pay_midpoint":  j.pay_midpoint,
                "loc_signal":    round(j.loc_signal or 0.0, 3),
                "us_eligible":   j.us_eligible,
                "injection":     j.injection_detected,
                "url":           j.url,
            })
        print(json.dumps(out, indent=2))
        return

    # ── Table output ──────────────────────────────────────────────────────────
    W_ID      = 4
    W_STATUS  = 18
    W_SOURCE  = 10
    W_COMPANY = 22
    W_TITLE   = 40
    W_TRACK   = 6
    W_RANK    = 7
    W_FIT     = 6
    W_PAY     = 8
    W_LOC     = 5
    W_SALARY  = 7

    header = (
        f"{'ID':>{W_ID}}  "
        f"{'Status':<{W_STATUS}}  "
        f"{'Src':<{W_SOURCE}}  "
        f"{'Company':<{W_COMPANY}}  "
        f"{'Title':<{W_TITLE}}  "
        f"{'Track':<{W_TRACK}}  "
        f"{'Rank':>{W_RANK}}  "
        f"{'Fit':>{W_FIT}}  "
        f"{'Pay$':>{W_PAY}}  "
        f"{'PaySig':>{W_LOC}}  "
        f"{'LocSig':>{W_LOC}}"
    )
    sep = "─" * len(header)
    print(sep)
    print(header)
    print(sep)

    # Stats buckets
    status_counts: dict[str, int] = {}

    for j in jobs:
        icon   = _status_icon(j.status or "")
        status = j.status or "?"
        status_counts[status] = status_counts.get(status, 0) + 1

        company = (j.company or "").replace("inc", "").replace("llc", "").title()
        company = company[:W_COMPANY]
        title   = (j.title or "")[:W_TITLE]
        track   = (j.track or "—")[:W_TRACK]

        rank       = f"{j.final_rank:.3f}"     if j.final_rank  is not None else "  —  "
        fit        = f"{j.base_fit:.3f}"       if j.base_fit    is not None else "  — "
        pay_signal = f"{j.pay_signal:.2f}"     if j.pay_signal  is not None else " — "
        loc_signal = f"{j.loc_signal:.2f}"     if j.loc_signal  is not None else " — "
        salary     = _pay_str(j.pay_midpoint)

        print(
            f"{j.id:>{W_ID}}  "
            f"{icon} {status:<{W_STATUS-2}}  "
            f"{j.source or '?':<{W_SOURCE}}  "
            f"{company:<{W_COMPANY}}  "
            f"{title:<{W_TITLE}}  "
            f"{track:<{W_TRACK}}  "
            f"{rank:>{W_RANK}}  "
            f"{fit:>{W_FIT}}  "
            f"{salary:>{W_PAY}}  "
            f"{pay_signal:>{W_LOC}}  "
            f"{loc_signal:>{W_LOC}}"
        )

    print(sep)
    total = len(jobs)
    summary_parts = [f"Total: {total}"]
    for st, cnt in sorted(status_counts.items(), key=lambda x: -x[1]):
        icon = _status_icon(st)
        summary_parts.append(f"{icon} {st}={cnt}")
    print("  |  ".join(summary_parts))


if __name__ == "__main__":
    main()
