"""Jobs-list + Job-detail query helpers.

Filters mirror the UI's ``JobFilters.tsx`` component: status (chip),
track, source, min/max rank, company contains, freshness window,
``us_eligible``, ``injection_detected``, and "has-applied" predicate.

We deliberately do per-field filters here (instead of a generic
search builder) so the OpenAPI schema documents every accepted query
param and the typegen step produces strict TS types.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from autoapply.tracker.models import Application, Job

from api.schemas import JobDetailOut, JobOut, JobScoring


def _to_out(j: Job, application_count: int = 0) -> JobOut:
    """ORM Job → JobOut. Compact projection for the list view."""
    return JobOut(
        id=j.id,
        canonical_key=j.canonical_key,
        source=j.source,
        board_token=j.board_token,
        url=j.url,
        title=j.title,
        company=j.company,
        location=j.location or "",
        department=j.department or "",
        employment_type=j.employment_type or "",
        posted_at=j.posted_at or "",
        track=j.track,
        status=j.status,
        us_eligible=j.us_eligible,
        injection_detected=j.injection_detected,
        scoring=JobScoring(
            base_fit=j.base_fit,
            pay_signal=j.pay_signal,
            pay_midpoint=j.pay_midpoint,
            loc_signal=j.loc_signal,
            freshness_signal=j.freshness_signal,
            final_rank=j.final_rank,
        ),
        application_count=application_count,
        created_at=j.created_at,
        updated_at=j.updated_at,
    )


def list_jobs(
    s: Session,
    *,
    status: list[str] | None = None,
    track: list[str] | None = None,
    source: list[str] | None = None,
    min_rank: float | None = None,
    max_rank: float | None = None,
    company_contains: str | None = None,
    posted_within_days: int | None = None,
    us_eligible: bool | None = None,
    injection_detected: bool | None = None,
    has_applied: bool | None = None,
    page: int = 1,
    page_size: int = 50,
    order_by: str = "final_rank",
) -> tuple[list[JobOut], int]:
    """Filter + sort + paginate jobs. Returns (rows, total_count)."""

    # Build a join that left-joins applications so we can count + filter
    # by has-applied without an N+1.
    apps_count = (
        select(Application.job_id, func.count(Application.id).label("cnt"))
        .group_by(Application.job_id)
        .subquery()
    )

    stmt = select(Job, func.coalesce(apps_count.c.cnt, 0)).outerjoin(
        apps_count, apps_count.c.job_id == Job.id
    )

    if status:
        stmt = stmt.where(Job.status.in_(status))
    if track:
        stmt = stmt.where(Job.track.in_(track))
    if source:
        stmt = stmt.where(Job.source.in_(source))
    if min_rank is not None:
        stmt = stmt.where(Job.final_rank >= min_rank)
    if max_rank is not None:
        stmt = stmt.where(Job.final_rank <= max_rank)
    if company_contains:
        stmt = stmt.where(Job.company.ilike(f"%{company_contains}%"))
    if posted_within_days is not None and posted_within_days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=posted_within_days)
        stmt = stmt.where(Job.created_at >= cutoff)
    if us_eligible is not None:
        stmt = stmt.where(Job.us_eligible == us_eligible)
    if injection_detected is not None:
        stmt = stmt.where(Job.injection_detected == injection_detected)
    if has_applied is True:
        stmt = stmt.where(func.coalesce(apps_count.c.cnt, 0) > 0)
    elif has_applied is False:
        stmt = stmt.where(func.coalesce(apps_count.c.cnt, 0) == 0)

    # Total before pagination — separate count query so we don't have to
    # fetch all rows first.
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = s.execute(count_stmt).scalar_one()

    # Order — default is highest final_rank first, NULLs last.
    if order_by == "final_rank":
        stmt = stmt.order_by(Job.final_rank.desc().nulls_last(), Job.id.desc())
    elif order_by == "created_at":
        stmt = stmt.order_by(Job.created_at.desc())
    elif order_by == "posted_at":
        stmt = stmt.order_by(Job.posted_at.desc(), Job.id.desc())
    else:
        stmt = stmt.order_by(Job.id.desc())

    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = s.execute(stmt).all()
    return [_to_out(j, cnt) for (j, cnt) in rows], int(total)


def get_job_detail(s: Session, job_id: int) -> JobDetailOut | None:
    """Single-job detail with related applications inlined."""
    j = s.get(Job, job_id)
    if j is None:
        return None

    # Pull all applications for this job (cheap — a job has 1-3 typically).
    apps_stmt = (
        select(Application)
        .where(Application.job_id == job_id)
        .order_by(Application.submitted_at.desc())
    )
    apps = s.execute(apps_stmt).scalars().all()

    # Translate via apps_service to avoid duplicating the projection.
    from api.services.apps_service import _to_application_out

    base = _to_out(j, application_count=len(apps))
    return JobDetailOut(
        **base.model_dump(),
        description=j.description or "",
        sightings=j.sightings or {},
        meta=j.meta or {},
        applications=[_to_application_out(a, j) for a in apps],
    )
