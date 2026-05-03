"""Aggregate LLM call metadata across all Application rows.

``Application.artifacts['batch_audit']`` is the canonical audit
shape. Fields we surface:
  - model_used        — final Gemini model the cascade settled on
  - batch_asked       — list of question keys passed to the LLM
  - cascade_trace     — list of attempted models
  - error             — model error message (or "")

We also surface application-velocity points for the Dashboard.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from autoapply.tracker.models import Application, Job

from api.schemas import LLMAuditRow, VelocityPoint


def list_llm_audit(s: Session, limit: int = 100) -> list[LLMAuditRow]:
    rows = s.execute(
        select(Application, Job)
        .join(Job, Job.id == Application.job_id)
        .order_by(Application.submitted_at.desc())
        .limit(limit * 2)
    ).all()

    out: list[LLMAuditRow] = []
    for a, j in rows:
        artifacts = a.artifacts or {}
        audit = artifacts.get("batch_audit") or {}
        model = (audit.get("model_used") or "").strip()
        if not model:
            continue
        out.append(
            LLMAuditRow(
                application_id=a.id,
                submitted_at=a.submitted_at,
                job_company=j.company,
                job_title=j.title,
                model_used=model,
                batch_asked_count=len(audit.get("batch_asked") or []),
                cascade_trace=list(audit.get("cascade_trace") or []),
                error=str(audit.get("error") or ""),
                answer_count=int(audit.get("answer_count", 0) or 0),
            )
        )
        if len(out) >= limit:
            break
    return out


def velocity_last_n_days(s: Session, days: int = 14) -> list[VelocityPoint]:
    """Per-day app counts for the dashboard sparkline."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = s.execute(
        select(Application).where(Application.submitted_at >= cutoff)
    ).scalars().all()

    by_day: dict[str, dict[str, int]] = defaultdict(
        lambda: {"apps": 0, "ok": 0, "failed": 0}
    )
    for a in rows:
        d = a.submitted_at.date().isoformat()
        bucket = by_day[d]
        bucket["apps"] += 1
        if a.outcome == "ok":
            bucket["ok"] += 1
        elif a.outcome == "failed":
            bucket["failed"] += 1

    # Fill gaps so the sparkline draws straight zeros instead of skipping
    out: list[VelocityPoint] = []
    today = datetime.now(timezone.utc).date()
    for n in range(days, -1, -1):
        d = (today - timedelta(days=n)).isoformat()
        b = by_day.get(d, {"apps": 0, "ok": 0, "failed": 0})
        out.append(VelocityPoint(date=d, apps=b["apps"], ok=b["ok"], failed=b["failed"]))
    return out
