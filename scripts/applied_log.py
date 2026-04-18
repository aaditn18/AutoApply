#!/usr/bin/env python3
"""
applied_log.py — Pretty-print every confirmed application.

Usage:
    python scripts/applied_log.py              # all outcomes
    python scripts/applied_log.py --ok-only    # only outcome=ok
    python scripts/applied_log.py --json       # machine-readable JSON
    python scripts/applied_log.py --today      # only today's applications

Columns (in display order):
    #  Timestamp  Platform  Company  Role  Track  Resume PDF  Score  Outcome
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, date
from pathlib import Path

# Make sure src/ is on the path when run from repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from autoapply.config import get_settings
from autoapply.tracker.db import create_engine_from_settings
from autoapply.tracker.models import Application, Job
from sqlalchemy.orm import Session


def _resume_name(track: str | None) -> str:
    if not track:
        return "—"
    return f"aadit_nilay_resume_{track}.pdf"


def _fmt_ts(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    return dt.strftime("%Y-%m-%d %H:%M")


def _outcome_icon(outcome: str) -> str:
    return {
        "ok":      "✅",
        "captcha": "🔒",
        "failed":  "❌",
        "review":  "👀",
        "dry_run": "🔍",
    }.get(outcome, "?")


def main() -> None:
    parser = argparse.ArgumentParser(description="Log of all submitted applications.")
    parser.add_argument("--ok-only",  action="store_true", help="Only show outcome=ok rows")
    parser.add_argument("--today",    action="store_true", help="Only today's applications")
    parser.add_argument("--json",     action="store_true", help="Output JSON instead of table")
    parser.add_argument("--no-dry",   action="store_true", help="Exclude dry-run rows (default: include)")
    args = parser.parse_args()

    engine = create_engine_from_settings()
    with Session(engine) as s:
        q = s.query(Application, Job).join(Job).order_by(Application.submitted_at)

        if args.ok_only:
            q = q.filter(Application.outcome == "ok")
        if args.no_dry:
            q = q.filter(Application.dry_run == False)  # noqa: E712
        if args.today:
            today_start = datetime.combine(date.today(), datetime.min.time())
            q = q.filter(Application.submitted_at >= today_start)

        rows = q.all()

    if not rows:
        print("No applications match the filter.")
        return

    if args.json:
        out = []
        for app, job in rows:
            out.append({
                "id":          app.id,
                "submitted_at": _fmt_ts(app.submitted_at),
                "platform":    job.source,
                "company":     job.company,
                "role":        job.title,
                "track":       app.track_submitted or job.track,
                "resume":      _resume_name(app.track_submitted or job.track),
                "score":       round(job.final_rank, 3),
                "base_fit":    round(job.base_fit or 0.5, 3),
                "pay_signal":  round(job.pay_signal or 0.0, 3),
                "loc_signal":  round(job.loc_signal or 0.0, 3),
                "outcome":     app.outcome,
                "dry_run":     app.dry_run,
                "url":         job.url,
                "final_url":   (app.artifacts or {}).get("final_url", ""),
            })
        print(json.dumps(out, indent=2))
        return

    # ── Table output ──────────────────────────────────────────────────────────
    # Column widths
    W_TS      = 16
    W_PLAT    = 11
    W_COMPANY = 22
    W_ROLE    = 38
    W_TRACK   = 6
    W_RESUME  = 28
    W_SCORE   = 7
    W_OUTCOME = 9

    header = (
        f"{'#':>4}  "
        f"{'Timestamp':<{W_TS}}  "
        f"{'Platform':<{W_PLAT}}  "
        f"{'Company':<{W_COMPANY}}  "
        f"{'Role':<{W_ROLE}}  "
        f"{'Track':<{W_TRACK}}  "
        f"{'Resume':<{W_RESUME}}  "
        f"{'Score':>{W_SCORE}}  "
        f"Outcome"
    )
    sep = "-" * len(header)
    print(sep)
    print(header)
    print(sep)

    ok_count     = 0
    captcha_count = 0
    failed_count  = 0
    review_count  = 0
    dry_count     = 0

    for i, (app, job) in enumerate(rows, 1):
        track   = app.track_submitted or job.track or "—"
        resume  = _resume_name(app.track_submitted or job.track)
        score   = f"{job.final_rank:.3f}"
        outcome = app.outcome or "?"
        icon    = _outcome_icon(outcome)
        dry_tag = " (dry)" if app.dry_run else ""

        company = (job.company or "").replace("inc", "").replace("llc", "").title()
        company = company[:W_COMPANY]
        role    = (job.title or "")[:W_ROLE]

        print(
            f"{i:>4}  "
            f"{_fmt_ts(app.submitted_at):<{W_TS}}  "
            f"{job.source:<{W_PLAT}}  "
            f"{company:<{W_COMPANY}}  "
            f"{role:<{W_ROLE}}  "
            f"{track:<{W_TRACK}}  "
            f"{resume:<{W_RESUME}}  "
            f"{score:>{W_SCORE}}  "
            f"{icon} {outcome}{dry_tag}"
        )

        # tally
        if outcome == "ok":     ok_count += 1
        elif outcome == "captcha": captcha_count += 1
        elif outcome == "failed":  failed_count += 1
        elif outcome == "review":  review_count += 1
        elif outcome == "dry_run": dry_count += 1

    print(sep)
    total = len(rows)
    print(
        f"Total: {total}  |  "
        f"✅ ok={ok_count}  "
        f"🔍 dry={dry_count}  "
        f"🔒 captcha={captcha_count}  "
        f"❌ failed={failed_count}  "
        f"👀 review={review_count}"
    )


if __name__ == "__main__":
    main()
