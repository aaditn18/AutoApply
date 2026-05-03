"""Applications-list + Application-detail query helpers.

The detail view's "Resolved fields" panel reads from
``Application.answers`` (the dict of every filled field) and joins
with ``Application.artifacts['batch_audit']`` to attach a per-field
``source`` tag. The audit shape is loosely-typed by design (LLM
fields can append new keys), so we treat unknown keys as
``"unknown"`` rather than crashing.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from autoapply.tracker.models import Application, Job, ReviewFlag

from api.schemas import (
    ApplicationDetailOut,
    ApplicationOut,
    ResolvedField,
    ReviewFlagOut,
)


def _resolved_field_count(answers: dict[str, Any] | None) -> int:
    if not answers:
        return 0
    return sum(1 for v in answers.values() if v not in (None, "", []))


def _llm_model_from_artifacts(artifacts: dict[str, Any] | None) -> str | None:
    if not artifacts:
        return None
    audit = artifacts.get("batch_audit") or {}
    return audit.get("model_used") or audit.get("model") or None


def _to_application_out(a: Application, j: Job) -> ApplicationOut:
    return ApplicationOut(
        id=a.id,
        job_id=a.job_id,
        job_title=j.title,
        job_company=j.company,
        job_source=j.source,
        track_submitted=a.track_submitted,
        dry_run=a.dry_run,
        outcome=a.outcome,
        error_code=a.error_code or "",
        error_message=a.error_message or "",
        resolved_field_count=_resolved_field_count(a.answers),
        llm_model=_llm_model_from_artifacts(a.artifacts),
        submitted_at=a.submitted_at,
    )


def _to_review_flag_out(f: ReviewFlag) -> ReviewFlagOut:
    return ReviewFlagOut(
        id=f.id,
        job_id=f.job_id,
        application_id=f.application_id,
        field_name=f.field_name or "",
        field_label=f.field_label or "",
        field_kind=f.field_kind or "",
        required=f.required,
        options=list(f.options or []),
        reason=f.reason,
        question_type=f.question_type,
        attempted_value=f.attempted_value or "",
        created_at=f.created_at,
    )


def list_applications(
    s: Session,
    *,
    outcome: list[str] | None = None,
    dry_run: bool | None = None,
    track: list[str] | None = None,
    source: list[str] | None = None,
    error_code_contains: str | None = None,
    submitted_within_days: int | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[ApplicationOut], int]:
    """Filter + paginate applications joined with their job. Returns (rows, total)."""
    stmt = select(Application, Job).join(Job, Job.id == Application.job_id)

    if outcome:
        stmt = stmt.where(Application.outcome.in_(outcome))
    if dry_run is not None:
        stmt = stmt.where(Application.dry_run == dry_run)
    if track:
        stmt = stmt.where(Application.track_submitted.in_(track))
    if source:
        stmt = stmt.where(Job.source.in_(source))
    if error_code_contains:
        stmt = stmt.where(Application.error_code.ilike(f"%{error_code_contains}%"))
    if submitted_within_days is not None and submitted_within_days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=submitted_within_days)
        stmt = stmt.where(Application.submitted_at >= cutoff)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = s.execute(count_stmt).scalar_one()

    stmt = stmt.order_by(Application.submitted_at.desc()).offset(
        (page - 1) * page_size
    ).limit(page_size)
    rows = s.execute(stmt).all()
    return [_to_application_out(a, j) for (a, j) in rows], int(total)


def _resolved_fields(
    answers: dict[str, Any] | None, artifacts: dict[str, Any] | None
) -> list[ResolvedField]:
    """Translate a flat answers dict into the per-field audit list.

    Source is inferred from ``artifacts.batch_audit.batch_asked`` (the
    list of keys the LLM was asked) and a couple of heuristic markers.
    Unknown sources fall back to ``"unknown"`` rather than crashing —
    the artifact shape evolved over time and not every old row carries
    the modern fields.
    """
    if not answers:
        return []

    audit = (artifacts or {}).get("batch_audit") or {}
    batch_asked: set[str] = set(audit.get("batch_asked") or [])

    # Heuristic source inference — keep narrow.
    PROFILE_KEYS = {
        "name", "first_name", "last_name", "email", "phone",
        "linkedin_url", "github_url", "current_location",
        "school_name", "degree", "major", "gpa", "graduation_year",
        "resume", "cover_letter",
    }
    BANK_HINTS = {
        "salary_expectation", "willing_to_relocate", "notice_period",
        "available_start_date", "referral_name", "referral_email",
        "agree_to_terms", "agree_to_comms", "background_check_consent",
        "work_authorized_us", "require_sponsorship_now",
        "require_sponsorship_future", "visa_status",
        "demo_gender", "demo_race", "demo_disability", "demo_veteran",
    }

    out: list[ResolvedField] = []
    for k, v in answers.items():
        if k in batch_asked:
            source = "llm"
        elif k in PROFILE_KEYS:
            source = "profile"
        elif k in BANK_HINTS or k.startswith("demo_"):
            source = "bank"
        else:
            source = "classifier"
        out.append(
            ResolvedField(
                label=k,
                kind=type(v).__name__ if v is not None else "null",
                value=v,
                source=source,
                confidence=None,
            )
        )
    return out


def _screenshot_url_for_job(job_url: str) -> str:
    """Replicate ``submitter/driver.py::_save_presubmit_screenshot`` hash.

    The naming is ``presubmit_{md5(url)[:10]}.png`` — same hash here
    means the file maps cleanly. Returns the URL the static-files
    mount will serve.
    """
    tag = hashlib.md5(job_url.encode()).hexdigest()[:10]
    return f"/api/static/screenshots/presubmit_{tag}.png"


def get_application_detail(s: Session, app_id: int) -> ApplicationDetailOut | None:
    """Single application with resolved fields + flags + screenshot."""
    row = s.execute(
        select(Application, Job)
        .join(Job, Job.id == Application.job_id)
        .where(Application.id == app_id)
    ).one_or_none()
    if row is None:
        return None
    a, j = row

    flags = (
        s.execute(
            select(ReviewFlag).where(ReviewFlag.application_id == app_id)
        )
        .scalars()
        .all()
    )

    base = _to_application_out(a, j)
    return ApplicationDetailOut(
        **base.model_dump(),
        answers=a.answers or {},
        cover_letter_text=a.cover_letter_text or "",
        artifacts=a.artifacts or {},
        resolved_fields=_resolved_fields(a.answers, a.artifacts),
        review_flags=[_to_review_flag_out(f) for f in flags],
        screenshot_url=_screenshot_url_for_job(j.url),
        job_url=j.url,
        job_description=j.description or "",
    )
