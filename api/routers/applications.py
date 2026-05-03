"""GET /api/applications and GET /api/applications/{id}."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from autoapply.tracker.models import Application

from api.deps import get_db
from api.schemas import (
    ApplicationDetailOut,
    BatchApplyIn,
    BatchApplyOut,
    Page,
    RetryOut,
)
from api.services import pipeline_runner
from api.services.apps_service import get_application_detail, list_applications


router = APIRouter(prefix="/api/applications", tags=["applications"])


@router.get("", response_model=Page)
def list_applications_route(
    outcome: list[str] | None = Query(None),
    dry_run: bool | None = Query(None),
    track: list[str] | None = Query(None),
    source: list[str] | None = Query(None),
    error_code: str | None = Query(None, max_length=64),
    submitted_within_days: int | None = Query(None, ge=1, le=365),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    s: Session = Depends(get_db),
) -> Page:
    items, total = list_applications(
        s,
        outcome=outcome,
        dry_run=dry_run,
        track=track,
        source=source,
        error_code_contains=error_code,
        submitted_within_days=submitted_within_days,
        page=page,
        page_size=page_size,
    )
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.get("/{app_id}", response_model=ApplicationDetailOut)
def get_application_route(
    app_id: int,
    s: Session = Depends(get_db),
) -> ApplicationDetailOut:
    out = get_application_detail(s, app_id)
    if out is None:
        raise HTTPException(
            status_code=404, detail=f"application {app_id} not found"
        )
    return out


@router.post("/{app_id}/retry", response_model=RetryOut)
async def retry_application(
    app_id: int,
    s: Session = Depends(get_db),
) -> RetryOut:
    """Re-run apply for the Job behind this Application.

    Spawns ``scripts/apply_by_job_ids.py`` with the original
    ``dry_run`` setting. Returns a ``run_id`` (the new application
    row gets created by the subprocess). UI polls ``/api/applications``
    after the run completes to find the new id.
    """
    a = s.get(Application, app_id)
    if a is None:
        raise HTTPException(status_code=404, detail=f"application {app_id} not found")
    run_id = await pipeline_runner.start_run(
        "apply-by-ids",
        job_ids=[a.job_id],
        dry_run=a.dry_run,
    )
    return RetryOut(
        ok=True,
        new_application_id=None,
        outcome="started",
        message=f"retry queued — run_id={run_id}",
    )


@router.post("/{app_id}/prefill", response_model=dict)
def prefill_application(
    app_id: int,
    s: Session = Depends(get_db),
) -> dict:
    """Open a non-headless browser pre-filled with the answers from
    a previously-failed Application.

    The user reviews the browser window (which will be every field
    already filled — name, email, EEO, consent boxes, etc.) and
    clicks Submit themselves once captcha / OTP / spam-flag is
    handled. We never click Submit on their behalf in this flow.

    Returns immediately with metadata; the browser session runs in
    a daemon thread on the API host. Requires the API to be running
    on the user's local machine (Playwright spawns a real Chromium
    window — won't work over SSH / Tailscale forwards).
    """
    from api.services.prefill_service import start_prefill_for_application

    try:
        return start_prefill_for_application(s, app_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/batch", response_model=BatchApplyOut)
async def batch_apply(body: BatchApplyIn) -> BatchApplyOut:
    """Apply to a list of Job ids in one subprocess.

    The CLI's existing pacing (15s default) handles rate-limiting;
    we don't override it here.
    """
    if not body.job_ids:
        raise HTTPException(status_code=400, detail="job_ids is empty")
    run_id = await pipeline_runner.start_run(
        "apply-by-ids",
        job_ids=body.job_ids,
        dry_run=body.dry_run,
    )
    return BatchApplyOut(started=len(body.job_ids), run_id=run_id)
