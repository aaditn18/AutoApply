"""Dashboard KPI computation.

All read-only; one round trip per query. Today's volume (~2k jobs,
~250 apps) makes a fully-recomputed dashboard cheap (<100ms).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from autoapply.tracker.models import Application, Job

from api.schemas import (
    DashboardOut,
    OutcomeCounts,
    SourceSpamRate,
    TopJobOut,
)
from api.services.review_service import _looks_like_spam_reject


def build_dashboard(s: Session) -> DashboardOut:
    """Compute every KPI tile in one call."""
    # ── Job counts ────────────────────────────────────────────────
    total_jobs = s.execute(select(func.count()).select_from(Job)).scalar_one()
    scored_jobs = s.execute(
        select(func.count()).select_from(Job).where(Job.status == "scored")
    ).scalar_one()
    unapplied_scored = s.execute(
        select(func.count())
        .select_from(Job)
        .outerjoin(Application, Application.job_id == Job.id)
        .where(Job.status == "scored")
        .where(Application.id.is_(None))
    ).scalar_one()

    # ── Application counts ────────────────────────────────────────
    apps_total = s.execute(
        select(func.count()).select_from(Application)
    ).scalar_one()

    cutoff_7d = datetime.now(timezone.utc) - timedelta(days=7)
    apps_last_7d = s.execute(
        select(func.count())
        .select_from(Application)
        .where(Application.submitted_at >= cutoff_7d)
    ).scalar_one()

    # ── Outcome breakdown ────────────────────────────────────────
    outcome_rows = s.execute(
        select(Application.outcome, func.count())
        .group_by(Application.outcome)
    ).all()
    outcomes = OutcomeCounts()
    for label, n in outcome_rows:
        if hasattr(outcomes, label):
            setattr(outcomes, label, int(n))

    # ── Spam-flag rate per ATS ──────────────────────────────────
    # Iterate failed rows once + apply the same heuristic used by
    # the review queue. Volume today is small (~150 failed); if it
    # grows, persist the marker into a column and switch to SQL.
    failed_rows = s.execute(
        select(Application, Job)
        .join(Job, Job.id == Application.job_id)
        .where(Application.outcome == "failed")
    ).all()

    by_source: dict[str, dict[str, int]] = {
        "greenhouse": {"failed": 0, "spam": 0},
        "lever": {"failed": 0, "spam": 0},
        "ashby": {"failed": 0, "spam": 0},
    }
    for a, j in failed_rows:
        bucket = by_source.get(j.source)
        if bucket is None:
            continue
        bucket["failed"] += 1
        if _looks_like_spam_reject(a, j):
            bucket["spam"] += 1

    spam_rates: list[SourceSpamRate] = []
    for src in ("greenhouse", "lever", "ashby"):
        b = by_source[src]
        rate = (b["spam"] / b["failed"]) if b["failed"] else 0.0
        spam_rates.append(
            SourceSpamRate(
                source=src,
                total_failed=b["failed"],
                spam_flagged=b["spam"],
                spam_rate=round(rate, 3),
            )
        )

    # ── Top 5 unapplied scored jobs ──────────────────────────────
    top_rows = s.execute(
        select(Job)
        .outerjoin(Application, Application.job_id == Job.id)
        .where(Job.status == "scored")
        .where(Application.id.is_(None))
        .order_by(Job.final_rank.desc().nulls_last())
        .limit(5)
    ).scalars().all()
    top = [
        TopJobOut(
            id=j.id,
            title=j.title,
            company=j.company,
            track=j.track,
            final_rank=j.final_rank,
            url=j.url,
        )
        for j in top_rows
    ]

    # Velocity sparkline — last 14 days.
    from api.services.llm_audit_service import velocity_last_n_days
    velocity = velocity_last_n_days(s, days=14)

    return DashboardOut(
        total_jobs=int(total_jobs),
        scored_jobs=int(scored_jobs),
        unapplied_scored=int(unapplied_scored),
        apps_total=int(apps_total),
        apps_last_7d=int(apps_last_7d),
        outcomes=outcomes,
        spam_rates=spam_rates,
        top_unapplied=top,
        velocity=velocity,
        last_updated=datetime.now(timezone.utc),
    )
