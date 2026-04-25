"""Re-run track assignment on existing scored jobs with track=NULL.

Context: added semantic-similarity fallback (step 3.5 in
``select/track_picker.py``) on 2026-04-21. Existing jobs in the DB
that were scored before this change may have ``track=NULL`` because
the old ladder abstained. This script re-runs ``pick_track`` on
those jobs and persists the new assignment.

By design it **only touches** jobs where ``track IS NULL AND
status='scored'`` — never overrides an existing good assignment.
Jobs in other statuses (``rejected_*``, ``applied_*``) are left
alone.

Usage:
    python scripts/reassign_tracks.py                 # all sources
    python scripts/reassign_tracks.py --source ashby  # one source
    python scripts/reassign_tracks.py --dry-run       # show changes, don't save
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from sqlalchemy.orm import Session

# Ensure repo is importable when invoked as a script.
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autoapply.config import get_settings
from autoapply.profile.build import load_profiles
from autoapply.select.track_picker import pick_track
from autoapply.tracker.db import create_engine_from_settings, init_db, session_scope
from autoapply.tracker.models import Job


log = logging.getLogger("reassign_tracks")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        choices=["greenhouse", "lever", "ashby", "all"],
        default="all",
        help="Limit to one source.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the proposed changes, don't persist.",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )

    settings = get_settings()
    engine = create_engine_from_settings(settings)
    init_db(engine)
    profiles = load_profiles(settings.profile_json_path)

    with Session(engine) as s:
        q = s.query(Job).filter(
            Job.status == "scored",
            Job.track.is_(None),
            Job.injection_detected.is_(False),
        )
        if args.source != "all":
            q = q.filter(Job.source == args.source)
        candidates = q.all()

    log.info("found %d scored jobs with track=NULL", len(candidates))

    per_source: dict[str, dict[str, int]] = {}
    updates: list[tuple[int, str, str, str]] = []  # (id, source, title, new_track)

    for job in candidates:
        decision = pick_track(
            title=job.title,
            description=job.description,
            profiles_by_track=profiles,
            injection_detected=False,
        )
        if decision.track in ("swe", "ml", "hpc", "quant"):
            src = job.source
            per_source.setdefault(src, {}).setdefault(decision.track, 0)
            per_source[src][decision.track] += 1
            updates.append((job.id, src, job.title or "", decision.track))

    print()
    print(f"{'source':<12} {'swe':<5} {'ml':<5} {'hpc':<5} {'quant':<6}")
    print("-" * 40)
    for src in sorted(per_source):
        counts = per_source[src]
        print(
            f"{src:<12} "
            f"{counts.get('swe', 0):<5} "
            f"{counts.get('ml', 0):<5} "
            f"{counts.get('hpc', 0):<5} "
            f"{counts.get('quant', 0):<6}"
        )
    print()
    print(f"{'source':<12} {'job_id':<8} {'track':<6} {'title':<60}")
    print("-" * 90)
    for (jid, src, title, track) in updates[:40]:
        print(f"{src:<12} {jid:<8} {track:<6} {title[:60]}")
    if len(updates) > 40:
        print(f"  ... and {len(updates) - 40} more")

    if args.dry_run:
        print()
        print(f"DRY-RUN: would update {len(updates)} jobs — rerun without --dry-run to persist.")
        return

    with session_scope(engine) as s:
        for (jid, _src, _title, new_track) in updates:
            job = s.get(Job, jid)
            if job is not None:
                job.track = new_track

    print()
    print(f"persisted: {len(updates)} track assignments.")


if __name__ == "__main__":
    main()
