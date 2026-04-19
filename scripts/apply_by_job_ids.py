#!/usr/bin/env python3
"""Apply to specific jobs by database ID — skips the per-company dedup logic.

Useful for surgical retries against exact jobs we want to re-test after a
classifier or filler fix. Reuses the same ``_apply_one`` helper that the
``apply_best_per_company.py`` script uses.

Usage:
    python scripts/apply_by_job_ids.py 1455 953 1368 617 --no-dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from apply_best_per_company import _apply_one, _load_profile_and_bank  # type: ignore
from autoapply.config import get_settings
from autoapply.tracker.db import create_engine_from_settings, init_db
from autoapply.tracker.models import Job
from sqlalchemy.orm import Session


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("apply_by_job_ids")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("ids", nargs="+", type=int, help="Job.id primary keys")
    parser.add_argument("--no-dry-run", dest="dry_run", action="store_false",
                        default=True, help="Real submit (default is dry-run)")
    parser.add_argument("--pace", type=float, default=15.0,
                        help="Seconds between applications")
    args = parser.parse_args()

    settings = get_settings()
    engine = create_engine_from_settings(settings)
    init_db(engine)

    profiles, bank = _load_profile_and_bank(settings)

    with Session(engine) as s:
        jobs = [s.get(Job, jid) for jid in args.ids]
        jobs = [j for j in jobs if j is not None]

    if not jobs:
        log.error("no jobs found for ids=%s", args.ids)
        sys.exit(1)

    print(f"Applying to {len(jobs)} job(s) | dry_run={args.dry_run}")
    for j in jobs:
        print(f"  id={j.id}  {j.company}: {j.title}  [{j.source}, track={j.track}, rank={j.final_rank}]")
    print()

    results = []
    for i, job in enumerate(jobs):
        log.info("[%d/%d] %s @ %s", i + 1, len(jobs), job.title, job.company)
        try:
            r = _apply_one(
                job,
                profiles=profiles,
                bank=bank,
                settings=settings,
                dry_run=args.dry_run,
                engine=engine,
            )
            results.append((job, r))
            log.info("  → outcome=%s  error=%s  field_errors=%s",
                     r.get("outcome"), r.get("error_code"), r.get("field_errors"))
        except Exception as exc:
            log.exception("apply error for job_id=%s: %s", job.id, exc)
            results.append((job, {"outcome": "error", "reason": str(exc)}))
        if i + 1 < len(jobs):
            time.sleep(args.pace)

    print()
    print("=" * 80)
    print("  RESULTS")
    print("=" * 80)
    for job, r in results:
        outcome = r.get("outcome", "?")
        err = r.get("error_code") or r.get("reason") or ""
        print(f"  {outcome:10s}  {job.company:30s}  {job.title[:50]:50s}  {err[:40]}")
    print("=" * 80)


if __name__ == "__main__":
    main()
