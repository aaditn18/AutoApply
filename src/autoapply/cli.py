"""AutoApply Typer CLI.

Entrypoints:
    autoapply ingest              # fetch GH + Lever boards → sqlite
    autoapply score               # apply selection+scoring to new jobs
    autoapply apply               # apply top-ranked jobs (DRY_RUN by default)
    autoapply profile-build       # rebuild state/profile.json from resume submodule
    autoapply review-handle       # poll open review issues, apply decisions
    autoapply security-report     # dump SecurityEvent rows from the past N days
    autoapply init-db             # create-all schema (idempotent)

All commands honor `Settings.DRY_RUN`. The `apply` command additionally
refuses to submit unless `--no-dry-run` is passed explicitly.
"""

from __future__ import annotations

import json
import logging
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import typer

from autoapply.answers.bank import AnswerBank
from autoapply.config import Settings, get_settings
from autoapply.congregate.cover_letter import (
    CoverLetterRejected,
    draft_cover_letter,
)
from autoapply.execute.base import Applicator
from autoapply.execute.greenhouse_apply import GreenhouseApplicator
from autoapply.execute.lever_apply import LeverApplicator
from autoapply.ingest.base import RawJob
from autoapply.ingest.greenhouse import GreenhouseSource
from autoapply.ingest.lever import LeverSource
from autoapply.profile.build import load_profiles, run as run_profile_build
from autoapply.profile.schema import Profile
from autoapply.review.gh_issues import GitHubIssueClient, build_payload
from autoapply.security.injection_guard import scan
from autoapply.select.dedup import canonical_key, filter_hard
from autoapply.select.location_filter import is_us_location, nyc_bonus
from autoapply.select.pay_extractor import extract_pay, pay_signal as pay_signal_fn
from autoapply.select.scorer import freshness_signal
from autoapply.select.track_picker import pick_track
from autoapply.select.yoe_filter import is_yoe_eligible
from autoapply.tracker.db import create_engine_from_settings, init_db, session_scope
from autoapply.tracker.models import (
    Application,
    Event,
    Job,
    SecurityEvent,
)


log = logging.getLogger("autoapply.cli")


app = typer.Typer(
    name="autoapply",
    help="Autonomous job-application agent.",
    no_args_is_help=True,
)


# -- Shared helpers ---------------------------------------------------------


def _configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=settings.LOG_LEVEL,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _read_companies_yaml(settings: Settings) -> tuple[list[str], list[str]]:
    """Return (greenhouse_tokens, lever_tokens).

    When TEST_SAFE_ONLY=True (the default), only the `test_safe` sub-list is
    returned. When False, both `test_safe` and `live_only` are merged.
    """
    import yaml

    path = Path(__file__).resolve().parent / "ingest" / "companies.yml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    def _tokens(ats_block: dict | list | None) -> list[str]:
        if ats_block is None:
            return []
        # Old flat-list format (backwards compat).
        if isinstance(ats_block, list):
            return [str(t) for t in ats_block]
        safe = [str(t) for t in (ats_block.get("test_safe") or [])]
        live = [str(t) for t in (ats_block.get("live_only") or [])]
        if settings.TEST_SAFE_ONLY:
            return safe
        return safe + live

    gh = _tokens(data.get("greenhouse"))
    lv = _tokens(data.get("lever"))
    return gh, lv


def _raw_to_job_kwargs(raw: RawJob) -> dict:
    return {
        "source": raw.source,
        "source_id": raw.source_id,
        "board_token": raw.board_token,
        "url": raw.url,
        "title": raw.title,
        "company": raw.company,
        "location": raw.location,
        "department": raw.department,
        "description": raw.description,
        "posted_at": raw.posted_at,
        "updated_at_source": raw.updated_at,
        "employment_type": raw.employment_type,
    }


def _load_profiles_or_exit(settings: Settings) -> dict[str, Profile]:
    path = settings.profile_json_path
    if not path.exists():
        typer.echo(
            f"profile.json missing at {path}. Run `autoapply profile-build` first.",
            err=True,
        )
        raise typer.Exit(code=2)
    return load_profiles(path)


def _load_bank_or_exit(settings: Settings) -> AnswerBank:
    path = settings.answer_bank_path
    if not path.exists():
        typer.echo(f"answer_bank.yml missing at {path}.", err=True)
        raise typer.Exit(code=2)
    return AnswerBank.from_path(path)


# -- Commands ---------------------------------------------------------------


@app.command("init-db")
def init_db_cmd() -> None:
    """Create all tables (idempotent). For MVP; prod should use Alembic."""
    settings = get_settings()
    _configure_logging(settings)
    engine = create_engine_from_settings(settings)
    init_db(engine)
    typer.echo(f"ok — schema created at {settings.database_url}")


@app.command("profile-build")
def profile_build_cmd() -> None:
    """Rebuild `state/profile.json` from the resume submodule."""
    settings = get_settings()
    _configure_logging(settings)
    out = run_profile_build(settings)
    typer.echo(f"ok — wrote {out}")


@app.command("ingest")
def ingest_cmd(
    source: Optional[str] = typer.Option(
        None, "--source", help="Only ingest this source (greenhouse|lever)."
    ),
    board: Optional[str] = typer.Option(
        None, "--board", help="Only ingest this board token (helpful for smoke tests)."
    ),
    limit: Optional[int] = typer.Option(
        None, "--limit", help="Cap total raw jobs ingested across sources."
    ),
) -> None:
    """Fetch boards and upsert new jobs into the DB."""
    settings = get_settings()
    _configure_logging(settings)
    engine = create_engine_from_settings(settings)
    init_db(engine)

    gh_tokens, lv_tokens = _read_companies_yaml(settings)
    if board:
        gh_tokens = [board] if source in (None, "greenhouse") else []
        lv_tokens = [board] if source in (None, "lever") else []
    elif source == "greenhouse":
        lv_tokens = []
    elif source == "lever":
        gh_tokens = []

    total = 0
    inserted = 0
    skipped = 0

    def _upsert(raw: RawJob) -> None:
        nonlocal total, inserted, skipped
        total += 1
        ck = canonical_key(raw.company, raw.title, raw.location)
        with session_scope(engine) as s:
            exists = s.query(Job.id).filter(Job.canonical_key == ck).first()
            if exists:
                skipped += 1
                return
            s.add(Job(canonical_key=ck, status="new", **_raw_to_job_kwargs(raw)))
            s.add(
                Event(
                    kind="ingested",
                    detail={"source": raw.source, "board": raw.board_token, "source_id": raw.source_id},
                )
            )
            inserted += 1

    if gh_tokens:
        with GreenhouseSource() as gh:
            for t in gh_tokens:
                for raw in gh.fetch_board(t):
                    if limit and total >= limit:
                        break
                    _upsert(raw)
    if lv_tokens and (not limit or total < limit):
        with LeverSource() as lv:
            for t in lv_tokens:
                for raw in lv.fetch_board(t):
                    if limit and total >= limit:
                        break
                    _upsert(raw)

    typer.echo(
        f"ingest done — seen={total} inserted={inserted} skipped={skipped}"
    )


@app.command("score")
def score_cmd(
    limit: Optional[int] = typer.Option(
        None, "--limit", help="Cap jobs scored this run (default: all `new`)."
    ),
) -> None:
    """Score + pick-track every `new` job; apply hard filters."""
    settings = get_settings()
    _configure_logging(settings)
    engine = create_engine_from_settings(settings)
    init_db(engine)

    profiles = _load_profiles_or_exit(settings)
    today = datetime.now(timezone.utc).date()

    # Build per-company last-application map for the 60-day cap.
    # Only counts non-dry-run applications so test runs don't poison the cap.
    from datetime import date as _date
    from sqlalchemy import func as _func
    with session_scope(engine) as s:
        cutoff_dt = datetime.now(timezone.utc) - timedelta(days=60)
        rows_recent = (
            s.query(Job.company, _func.max(Application.submitted_at).label("latest"))
            .join(Application, Application.job_id == Job.id)
            .filter(Application.submitted_at >= cutoff_dt, Application.dry_run == False)  # noqa: E712
            .group_by(Job.company)
            .all()
        )
    recent_company_apps: dict[str, _date] = {
        row.company.lower().strip(): row.latest.date()
        for row in rows_recent
        if row.latest is not None
    }

    scored = 0
    rejected_loc = 0
    rejected_inj = 0
    rejected_yoe = 0
    scored_ok = 0

    with session_scope(engine) as s:
        q = s.query(Job).filter(Job.status == "new").order_by(Job.created_at.asc())
        if limit:
            q = q.limit(limit)
        rows = q.all()

    for job_row in rows:
        # Scope each job in its own transaction for resilience.
        with session_scope(engine) as s:
            job = s.get(Job, job_row.id)
            if job is None:
                continue

            # 1) Injection scan on the full description.
            jd_report = scan(job.description or "")
            if jd_report.detected:
                job.injection_detected = True
                for hit in jd_report.hits:
                    s.add(
                        SecurityEvent(
                            job_id=job.id,
                            kind="injection_attempt",
                            pattern_matched=hit.kind.value,
                            snippet=hit.match_text[:256],
                            source_url=job.url,
                            severity="medium",
                        )
                    )

            # 2) Hard filters: US location + injection + 60-day company cap.
            us = is_us_location(job.location)
            hard = filter_hard(
                is_us=us,
                injection_detected=job.injection_detected,
                company=job.company,
                recent_company_applications=recent_company_apps,
                today=today,
            )
            if not hard.accepted:
                if hard.reason == "rejected_by_location":
                    job.status = "rejected_by_location"
                    rejected_loc += 1
                elif hard.reason == "rejected_by_injection":
                    job.status = "rejected_by_injection"
                    rejected_inj += 1
                else:
                    job.status = hard.reason  # rejected_by_company_cap etc.
                job.us_eligible = us
                scored += 1
                continue

            # 2.5) YOE filter — reject jobs that explicitly require > 2 years.
            if not is_yoe_eligible(job.description or ""):
                job.status = "rejected_by_yoe"
                job.us_eligible = us
                rejected_yoe += 1
                scored += 1
                continue

            # 3) Track pick — content-only, no firm list.
            decision = pick_track(
                title=job.title,
                description=job.description,
                profiles_by_track=profiles,
                injection_detected=job.injection_detected,
            )
            job.track = decision.track if decision.track in ("swe", "ml", "hpc", "quant") else None

            # 4) Pay + location + freshness signals.
            pay = extract_pay(job.description or "")
            if pay is not None:
                job.pay_midpoint = pay.midpoint
            job.pay_signal = pay_signal_fn(pay.midpoint if pay else None)
            job.loc_signal = nyc_bonus(job.location or "")
            job.freshness_signal = freshness_signal(job.posted_at or "")

            # 5) Base fit — deterministic placeholder (0.5). Gemini fit-score
            #    will replace this when wired; stored separately for offline re-rank.
            job.base_fit = 0.5

            job.final_rank = (
                (job.base_fit or 0.0)
                + (job.pay_signal or 0.0)
                + (job.loc_signal or 0.0)
                + (job.freshness_signal or 0.0)
            )
            job.us_eligible = us
            job.status = "scored"
            s.add(
                Event(
                    kind="scored",
                    job_id=job.id,
                    detail={
                        "track": job.track,
                        "final_rank": job.final_rank,
                        "pay_midpoint": job.pay_midpoint,
                        "base_fit": job.base_fit,
                    },
                )
            )
            scored_ok += 1
            scored += 1

    typer.echo(
        f"score done — total={scored} ok={scored_ok} "
        f"rej_location={rejected_loc} rej_injection={rejected_inj} "
        f"rej_yoe={rejected_yoe}"
    )


@app.command("apply")
def apply_cmd(
    no_dry_run: bool = typer.Option(
        False, "--no-dry-run", help="Actually submit (requires Playwright path, disabled in MVP)."
    ),
    limit: Optional[int] = typer.Option(
        None, "--limit", help="Override MAX_APPLICATIONS_PER_RUN for this invocation."
    ),
    min_rank: Optional[float] = typer.Option(
        None, "--min-rank", help="Override MIN_FINAL_RANK for this invocation."
    ),
) -> None:
    """Apply to top-ranked `scored` jobs. DRY_RUN by default."""
    settings = get_settings()
    _configure_logging(settings)
    engine = create_engine_from_settings(settings)
    init_db(engine)

    profiles = _load_profiles_or_exit(settings)
    bank = _load_bank_or_exit(settings)
    effective_dry_run = settings.DRY_RUN and not no_dry_run
    cap = limit or settings.MAX_APPLICATIONS_PER_RUN
    min_final_rank = min_rank if min_rank is not None else settings.MIN_FINAL_RANK

    # Daily cap check — only enforced for real (non-dry-run) submissions so
    # test runs don't burn into the quota.
    if not effective_dry_run:
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        with session_scope(engine) as s:
            today_count = (
                s.query(Application)
                .filter(
                    Application.submitted_at >= today_start,
                    Application.dry_run == False,  # noqa: E712
                    Application.outcome == "ok",
                )
                .count()
            )
        if today_count >= settings.MAX_APPLICATIONS_PER_DAY:
            typer.echo(
                f"apply aborted — daily cap reached "
                f"({today_count}/{settings.MAX_APPLICATIONS_PER_DAY} "
                f"real submissions today).",
                err=True,
            )
            raise typer.Exit(code=0)
        # Reduce this run's cap so we don't exceed the daily limit.
        remaining_today = settings.MAX_APPLICATIONS_PER_DAY - today_count
        cap = min(cap, remaining_today)

    with session_scope(engine) as s:
        rows = (
            s.query(Job)
            .filter(Job.status == "scored")
            .filter(Job.final_rank.is_not(None))
            .filter(Job.final_rank >= min_final_rank)
            # Only fetch jobs with an assigned track — track=None jobs cannot
            # be applied to (no resume to pick) and must not consume the cap.
            .filter(Job.track.in_(("swe", "ml", "hpc", "quant")))
            .order_by(Job.final_rank.desc())
            .limit(cap)
            .all()
        )
        row_ids = [r.id for r in rows]

    applied = 0
    reviews = 0
    failures = 0

    for jid in row_ids:
        with session_scope(engine) as s:
            job = s.get(Job, jid)
            if job is None or job.track not in ("swe", "ml", "hpc", "quant"):
                continue

            profile = profiles.get(job.track)
            if profile is None:
                log.warning("profile missing for track=%s; skipping job=%s", job.track, job.canonical_key)
                continue

            # Cover letter (template generator; swap for LLM in future).
            try:
                cl = draft_cover_letter(
                    profile=profile,
                    track=job.track,
                    job_title=job.title,
                    company=job.company,
                    job_description=job.description,
                    source_url=job.url,
                )
                cover_letter_text = cl.text
            except CoverLetterRejected as exc:
                # Output-level injection leak → log + route to review.
                for hit in exc.report.hits:
                    s.add(
                        SecurityEvent(
                            job_id=job.id,
                            kind="cover_letter_injection_leak",
                            pattern_matched=hit.kind.value,
                            snippet=hit.match_text[:256],
                            source_url=job.url,
                            severity="high",
                        )
                    )
                job.status = "queued_review"
                reviews += 1
                continue

            applicator = _make_applicator(
                job, profile=profile, bank=bank, track=job.track,
                cover_letter_text=cover_letter_text, settings=settings,
            )
            if applicator is None:
                job.status = "applied_failed"
                failures += 1
                continue

            result = applicator.apply(job, dry_run=effective_dry_run)

            s.add(
                Application(
                    job_id=job.id,
                    track_submitted=job.track,
                    dry_run=effective_dry_run,
                    outcome=result.outcome,
                    error_code=result.error_code,
                    error_message=result.error_message,
                    answers=result.answers,
                    cover_letter_text=cover_letter_text,
                    artifacts=result.artifacts,
                )
            )
            s.add(
                Event(
                    kind=f"apply:{result.outcome}",
                    job_id=job.id,
                    detail={
                        "dry_run": effective_dry_run,
                        "review_reasons": result.review_reasons,
                        "error_code": result.error_code,
                    },
                )
            )

            if result.outcome == "review":
                job.status = "queued_review"
                reviews += 1
            elif result.outcome == "dry_run":
                job.status = "applied_ok"  # optimistic; --no-dry-run promotes later
                applied += 1
            elif result.outcome == "ok":
                job.status = "applied_ok"
                applied += 1
                # Pace real submissions: random delay so we don't hammer ATS.
                if not effective_dry_run:
                    delay = random.uniform(
                        settings.APPLY_DELAY_SECONDS_MIN,
                        settings.APPLY_DELAY_SECONDS_MAX,
                    )
                    log.debug("pacing: sleeping %.0fs before next submission", delay)
                    time.sleep(delay)
            elif result.outcome == "captcha":
                job.status = "applied_captcha"
                failures += 1
            else:
                job.status = "applied_failed"
                failures += 1

    typer.echo(
        f"apply done — dry_run={effective_dry_run} applied={applied} "
        f"reviews={reviews} failures={failures}"
    )


def _make_applicator(
    job: Job,
    *,
    profile: Profile,
    bank: AnswerBank,
    track: str,
    cover_letter_text: str,
    settings: Settings,
) -> Applicator | None:
    resume_pdf = settings.resumes_dir / f"aadit_nilay_resume_{track}.pdf"
    if not resume_pdf.exists():
        log.error(
            "resume PDF not found for track=%s: %s — skipping job %s",
            track, resume_pdf, job.canonical_key,
        )
        return None
    resume_path = str(resume_pdf)
    kwargs = dict(
        profile=profile,
        bank=bank,
        track=track,
        resume_path=resume_path,
        cover_letter_text=cover_letter_text,
        dry_runs_dir=settings.dry_runs_dir,
    )
    if job.source == "greenhouse":
        return GreenhouseApplicator(**kwargs)
    if job.source == "lever":
        return LeverApplicator(**kwargs)
    return None


@app.command("security-report")
def security_report_cmd(
    days: int = typer.Option(7, "--days", help="Look-back window in days."),
) -> None:
    """Dump recent SecurityEvent rows (injection attempts, output leaks)."""
    settings = get_settings()
    _configure_logging(settings)
    engine = create_engine_from_settings(settings)
    init_db(engine)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    with session_scope(engine) as s:
        rows = (
            s.query(SecurityEvent)
            .filter(SecurityEvent.created_at >= cutoff)
            .order_by(SecurityEvent.created_at.desc())
            .all()
        )
        records = [
            {
                "id": r.id,
                "ts": r.created_at.isoformat() if r.created_at else None,
                "kind": r.kind,
                "pattern": r.pattern_matched,
                "severity": r.severity,
                "job_id": r.job_id,
                "source_url": r.source_url,
                "snippet": r.snippet[:200],
            }
            for r in rows
        ]
    typer.echo(json.dumps({"window_days": days, "count": len(records), "events": records}, indent=2))


@app.command("review-handle")
def review_handle_cmd(
    event_file: Path = typer.Option(
        ..., "--event", help="Path to the GitHub issue_comment event JSON (GITHUB_EVENT_PATH)."
    ),
    issues_client: bool = typer.Option(
        False, "--issues-client", help="Use the real GitHub API client (needs GH_ISSUES_PAT)."
    ),
) -> None:
    """Apply an approve/reject/snooze decision from a GitHub issue comment."""
    from autoapply.review.approval_listener import handle_comment_event
    settings = get_settings()
    _configure_logging(settings)
    if not event_file.exists():
        typer.echo(f"event file missing: {event_file}", err=True)
        raise typer.Exit(code=2)
    event = json.loads(event_file.read_text(encoding="utf-8"))
    out = handle_comment_event(event, allowed_actor=settings.GH_REPO_OWNER)
    typer.echo(json.dumps({
        "decision": out.decision.action,
        "track_override": out.decision.track_override,
        "canonical_key": out.canonical_key,
        "authorized": out.authorized,
        "issue_number": out.issue_number,
        "ignored_reason": out.ignored_reason,
    }, indent=2))

    if out.decision.action != "approve":
        return

    if not issues_client:
        typer.echo("approve parsed; --issues-client not set so skipping real submission", err=True)
        return

    if not settings.GH_ISSUES_PAT:
        typer.echo("GH_ISSUES_PAT required for --issues-client", err=True)
        raise typer.Exit(code=2)

    # Real approval flow is wired in Phase 2 once submit is enabled; for
    # MVP the listener just parses and logs.


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
