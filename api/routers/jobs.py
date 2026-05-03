"""GET /api/jobs and GET /api/jobs/{id}."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api.deps import get_db
from api.schemas import JobDetailOut, Page
from api.services.jobs_service import get_job_detail, list_jobs


router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("", response_model=Page)
def list_jobs_route(
    status: list[str] | None = Query(None),
    track: list[str] | None = Query(None),
    source: list[str] | None = Query(None),
    min_rank: float | None = Query(None, ge=0.0, le=2.0),
    max_rank: float | None = Query(None, ge=0.0, le=2.0),
    company: str | None = Query(None, max_length=128),
    posted_within_days: int | None = Query(None, ge=1, le=365),
    us_eligible: bool | None = Query(None),
    injection_detected: bool | None = Query(None),
    has_applied: bool | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    order_by: str = Query("final_rank"),
    s: Session = Depends(get_db),
) -> Page:
    items, total = list_jobs(
        s,
        status=status,
        track=track,
        source=source,
        min_rank=min_rank,
        max_rank=max_rank,
        company_contains=company,
        posted_within_days=posted_within_days,
        us_eligible=us_eligible,
        injection_detected=injection_detected,
        has_applied=has_applied,
        page=page,
        page_size=page_size,
        order_by=order_by,
    )
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.get("/{job_id}", response_model=JobDetailOut)
def get_job_route(
    job_id: int,
    s: Session = Depends(get_db),
) -> JobDetailOut:
    out = get_job_detail(s, job_id)
    if out is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return out
