#!/usr/bin/env python3
"""
apply_best_per_company.py — Apply to the highest-scored job at each unique company.

For every company that has scored jobs not yet applied to, this script picks the
single best-ranked job and submits one application per company. Supports
Greenhouse, Lever, or both simultaneously.

Usage:
    # Dry run (default — inspect what would be submitted):
    python scripts/apply_best_per_company.py
    python scripts/apply_best_per_company.py --source lever
    python scripts/apply_best_per_company.py --source both

    # Real submissions:
    python scripts/apply_best_per_company.py --no-dry-run

    # Real submissions, limit to top N companies by rank:
    python scripts/apply_best_per_company.py --no-dry-run --limit 10

    # Show plan without running anything:
    python scripts/apply_best_per_company.py --plan
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from autoapply.config import get_settings
from autoapply.answers.bank import AnswerBank
from autoapply.congregate.cover_letter import CoverLetterRejected, CoverLetterResult, draft_cover_letter
from autoapply.execute.ashby_apply import AshbyApplicator
from autoapply.execute.greenhouse_apply import GreenhouseApplicator
from autoapply.execute.lever_apply import LeverApplicator
from autoapply.profile.schema import Profile
from autoapply.tracker.db import create_engine_from_settings, init_db, session_scope
from autoapply.tracker.models import (
    Application,
    Event,
    Job,
    SecurityEvent,
    persist_review_flags,
)
from autoapply.security.injection_guard import InjectionDetected
from sqlalchemy.orm import Session

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("apply_best_per_company")

# Suppress noisy sub-loggers
for _noisy in ("httpx", "httpcore", "google_genai"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

APPLY_DELAY_MIN = 30   # seconds between real submissions
APPLY_DELAY_MAX = 90


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_profile_and_bank(settings) -> tuple[dict[str, Profile], AnswerBank]:
    import json as _json
    raw = _json.loads(settings.profile_json_path.read_text())
    profiles: dict[str, Profile] = {
        track: Profile(**data) for track, data in raw.items()
    }
    bank = AnswerBank.from_path(settings.answer_bank_path)
    return profiles, bank


def _best_per_company(
    engine,
    source: str = "greenhouse",
    *,
    retry_non_ok: bool = False,
    statuses: tuple[str, ...] = ("scored",),
) -> list[Job]:
    """Return the highest-ranked job per company not yet successfully applied to.

    Args:
        source: ``"greenhouse"``, ``"lever"``, ``"ashby"``, or ``"both"``.
        retry_non_ok: When True, jobs whose only prior ``Application`` rows
            had outcomes other than ``"ok"`` (i.e. ``failed`` / ``review`` /
            ``captcha`` / ``dry_run``) are eligible to be re-picked. Default
            False excludes every job with ANY ``Application`` row so we
            never submit twice. Use ``--retry-non-ok`` to rerun against
            fields the last attempt tripped on.
        statuses: Job.status values to consider. Defaults to just
            ``"scored"`` but the caller can widen to include
            ``"queued_review"`` / ``"applied_failed"`` when retrying.

    Companies are disambiguated per-source via a ``{source}:{company}`` key
    so the same company on two boards doesn't collapse.
    """
    with Session(engine) as s:
        # Jobs with a real successful application — ALWAYS excluded.
        ok_ids = {
            row[0]
            for row in s.query(Application.job_id)
            .filter(Application.outcome == "ok", Application.dry_run.is_(False))
            .all()
        }
        # Companies with ANY successful real application — every posting at
        # these companies is excluded, regardless of job_id. Prevents us
        # from double-applying to one company with different postings in
        # the same session (60-day-per-company cap runs at scoring, not
        # here; this is the apply-stage equivalent).
        ok_companies = {
            row[0]
            for row in (
                s.query(Job.company)
                .join(Application, Application.job_id == Job.id)
                .filter(Application.outcome == "ok", Application.dry_run.is_(False))
                .distinct()
                .all()
            )
        }
        if retry_non_ok:
            # Only exclude truly-OK jobs; allow re-picking ones whose
            # Application rows were failed/review/captcha/dry_run. BUT
            # still exclude the entire company if a sibling job already
            # succeeded — no double-submits at one company in one pass.
            excluded = ok_ids
        else:
            # Strict dedup: exclude every job with any Application row.
            applied_ids = {row[0] for row in s.query(Application.job_id).all()}
            excluded = applied_ids | ok_ids
        q = s.query(Job).filter(
            Job.status.in_(list(statuses)),
            Job.track.in_(["swe", "ml", "hpc", "quant"]),
            ~Job.id.in_(excluded),
        )
        if source == "both":
            q = q.filter(Job.source.in_(["greenhouse", "lever", "ashby"]))
        else:
            q = q.filter(Job.source == source)
        jobs = q.order_by(Job.final_rank.desc()).all()

    best: dict[str, Job] = {}
    for j in jobs:
        # Skip jobs at companies that already have a successful real submit.
        if (j.company or "") in ok_companies:
            continue
        # Key by source+company so the same company name on GH and Lever
        # doesn't collapse into a single entry.
        key = f"{j.source}:{j.company}"
        if key not in best or (j.final_rank or 0) > (best[key].final_rank or 0):
            best[key] = j

    # Return sorted highest-rank-first
    return sorted(best.values(), key=lambda j: -(j.final_rank or 0))


def _apply_one(
    job: Job,
    *,
    profiles: dict[str, Profile],
    bank: AnswerBank,
    settings,
    dry_run: bool,
    engine,
) -> dict:
    """Apply to a single job. Returns a result dict."""
    track = job.track
    profile = profiles.get(track) or profiles.get("swe")

    resume_pdf = settings.resumes_dir / f"aadit_nilay_resume_{track}.pdf"
    if not resume_pdf.exists():
        return {"outcome": "skip", "reason": f"resume not found: {resume_pdf}"}

    # Cover letter
    cover_letter_text = ""
    try:
        cl_result: CoverLetterResult = draft_cover_letter(
            job_title=job.title,
            company=job.company,
            job_description=job.description or "",
            profile=profile,
            track=track,
        )
        cover_letter_text = cl_result.text
    except (CoverLetterRejected, InjectionDetected) as exc:
        with session_scope(engine) as s:
            s.add(SecurityEvent(
                job_id=job.id,
                kind="injection_attempt",
                snippet=str(exc)[:500],
                pattern_matched="cover_letter_generation",
            ))
        log.warning("injection in cover letter for %s — using blank", job.canonical_key)

    applicator_cls = {
        "greenhouse": GreenhouseApplicator,
        "lever": LeverApplicator,
        "ashby": AshbyApplicator,
    }.get(job.source)
    if applicator_cls is None:
        return {"outcome": "skip", "reason": f"unsupported source: {job.source}"}

    applicator = applicator_cls(
        profile=profile,
        bank=bank,
        track=track,
        resume_path=str(resume_pdf),
        cover_letter_text=cover_letter_text,
        dry_runs_dir=settings.profile_json_path.parent / "dry_runs",
    )

    try:
        result = applicator.apply(job, dry_run=dry_run)
    finally:
        applicator.close()

    # Persist
    with session_scope(engine) as s:
        app = Application(
            job_id=job.id,
            track_submitted=track,
            dry_run=dry_run,
            outcome=result.outcome,
            error_code=result.error_code or "",
            error_message=result.error_message or "",
            answers=result.answers or {},
            cover_letter_text=cover_letter_text,
            artifacts=result.artifacts or {},
            submitted_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        s.add(app)
        s.flush()
        s.add(Event(
            job_id=job.id,
            kind=f"applied_{result.outcome}",
            detail={"dry_run": dry_run, "application_id": app.id},
        ))

        # Persist per-field review flags when the application needs human review.
        if result.outcome == "review":
            n_flags = persist_review_flags(
                s,
                job_id=job.id,
                application_id=app.id,
                flags=result.review_flags,
            )
            if n_flags:
                log.info("  flagged %d field(s) for review", n_flags)

        # Update job status
        j = s.get(Job, job.id)
        if result.outcome == "ok":
            j.status = "applied_ok"
        elif result.outcome == "review":
            j.status = "queued_review"
        elif result.outcome == "captcha":
            j.status = "applied_captcha"
        elif result.outcome == "dry_run":
            j.status = "applied_ok"
        else:
            j.status = "applied_failed"

    return {
        "outcome":  result.outcome,
        "company":  job.company,
        "title":    job.title,
        "track":    track,
        "rank":     job.final_rank,
        "error":    result.error_message or "",
        "field_errors": (result.artifacts or {}).get("field_errors", []),
        "final_url": (result.artifacts or {}).get("final_url", ""),
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply to the best job at each unique company (GH / Lever)."
    )
    parser.add_argument("--no-dry-run", action="store_true",
                        help="Submit real applications (default: dry run)")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max companies to apply to (0 = all)")
    parser.add_argument("--plan", action="store_true",
                        help="Just print the plan, don't apply anything")
    parser.add_argument("--min-rank", type=float, default=0.0,
                        help="Minimum final_rank to include (default 0.0)")
    parser.add_argument("--source", choices=["greenhouse", "lever", "ashby", "both"],
                        default="greenhouse",
                        help="Which ATS to pull candidates from (default: greenhouse)")
    parser.add_argument("--board-token", default="",
                        help="Restrict to a specific board token (e.g. 'whoop'). "
                             "Useful for smoke-testing a single Lever company.")
    parser.add_argument(
        "--retry-non-ok", action="store_true",
        help="Re-pick jobs whose only prior Application rows had outcomes "
             "OTHER than 'ok' (failed / review / captcha / dry_run). Use "
             "after fixing a classifier / bank bug that caused the earlier "
             "attempt to miss. Real OK submissions are never re-targeted.",
    )
    parser.add_argument(
        "--include-statuses", default="",
        help="Comma-separated Job.status values to include. Default is just "
             "'scored'. Use 'scored,queued_review,applied_failed' with "
             "--retry-non-ok to retry everything that didn't cleanly submit.",
    )
    args = parser.parse_args()

    dry_run = not args.no_dry_run
    settings = get_settings()
    engine = create_engine_from_settings()
    init_db(engine)  # idempotent; ensures review_flags table exists

    if args.include_statuses:
        statuses = tuple(s.strip() for s in args.include_statuses.split(",") if s.strip())
    elif args.retry_non_ok:
        # Common case: previous attempts landed these jobs in
        # queued_review or applied_failed; include those by default when
        # --retry-non-ok is set so the caller doesn't have to pass both.
        statuses = ("scored", "queued_review", "applied_failed")
    else:
        statuses = ("scored",)

    candidates = _best_per_company(
        engine,
        source=args.source,
        retry_non_ok=args.retry_non_ok,
        statuses=statuses,
    )
    if args.board_token:
        candidates = [j for j in candidates if j.board_token == args.board_token]

    # Apply rank filter
    if args.min_rank > 0:
        candidates = [j for j in candidates if j.final_rank >= args.min_rank]

    # Apply limit
    if args.limit:
        candidates = candidates[:args.limit]

    if not candidates:
        log.info("No eligible jobs found.")
        return

    # ── Print the plan ───────────────────────────────────────────────────────
    print()
    print(f"{'='*96}")
    print(f"  PLAN — {'DRY RUN' if dry_run else '⚡ REAL SUBMISSIONS'}  |  "
          f"source={args.source}  |  {len(candidates)} companies")
    print(f"{'='*96}")
    print(f"  {'#':>3}  {'Src':4}  {'Rank':>6}  {'Track':5}  {'Company':28}  {'Best Job'}")
    print(f"  {'-'*3}  {'-'*4}  {'-'*6}  {'-'*5}  {'-'*28}  {'-'*40}")
    for i, j in enumerate(candidates, 1):
        src_tag = (j.source or "?")[:4]
        print(f"  {i:>3}  {src_tag:4}  {j.final_rank:.3f}  {j.track:5}  "
              f"{j.company[:28]:28}  {j.title[:45]}")
    print(f"{'='*96}")
    print()

    if args.plan:
        return

    profiles, bank = _load_profile_and_bank(settings)

    # ── Apply ────────────────────────────────────────────────────────────────
    results = []
    ok = captcha = failed = review = skipped = 0

    for i, job in enumerate(candidates, 1):
        log.info("[%d/%d] %s @ %s (rank=%.3f, track=%s)",
                 i, len(candidates), job.title[:45], job.company, job.final_rank, job.track)

        r = _apply_one(
            job,
            profiles=profiles,
            bank=bank,
            settings=settings,
            dry_run=dry_run,
            engine=engine,
        )
        results.append(r)

        outcome = r["outcome"]
        icon = {"ok":"✅","dry_run":"🔍","captcha":"🔒","failed":"❌","review":"👀","skip":"⏭"}.get(outcome,"?")
        fe = r.get("field_errors") or []
        suffix = f"  field_errors={fe}" if fe else ""
        log.info("  → %s %s%s", icon, outcome, suffix)

        if   outcome in ("ok", "dry_run"): ok += 1
        elif outcome == "captcha":  captcha += 1
        elif outcome == "failed":   failed  += 1
        elif outcome == "review":   review  += 1
        elif outcome == "skip":     skipped += 1

        # Pace real submissions
        if not dry_run and outcome == "ok" and i < len(candidates):
            delay = random.uniform(APPLY_DELAY_MIN, APPLY_DELAY_MAX)
            log.info("  pacing: sleeping %.0fs before next apply…", delay)
            time.sleep(delay)

    # ── Summary ──────────────────────────────────────────────────────────────
    total = len(results)
    print()
    print(f"{'='*90}")
    print(f"  RESULTS — {'DRY RUN' if dry_run else 'REAL'}")
    print(f"{'='*90}")
    print(f"  {'#':>3}  {'Outcome':10}  {'Rank':>6}  {'Track':5}  {'Company':28}  {'Title'}")
    print(f"  {'-'*3}  {'-'*10}  {'-'*6}  {'-'*5}  {'-'*28}  {'-'*38}")
    for i, r in enumerate(results, 1):
        icon = {"ok":"✅","dry_run":"🔍","captcha":"🔒","failed":"❌","review":"👀","skip":"⏭"}.get(r["outcome"],"?")
        fe = r.get("field_errors") or []
        fe_tag = f" ⚠{len(fe)}" if fe else ""
        print(f"  {i:>3}  {icon} {r['outcome']:8}  {r['rank']:.3f}  {r['track']:5}  "
              f"{r['company'][:28]:28}  {r['title'][:38]}{fe_tag}")
    print(f"{'='*90}")
    print(f"  Total: {total}  |  "
          f"✅ {'ok' if not dry_run else 'dry_run'}={ok}  "
          f"🔒 captcha={captcha}  ❌ failed={failed}  "
          f"👀 review={review}  ⏭ skip={skipped}")
    print(f"{'='*90}")
    print()


if __name__ == "__main__":
    main()
