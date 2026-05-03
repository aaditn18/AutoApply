"""Review queue helpers — split into spam-flag and review_flags tabs.

Spam-flagged: ``Application.outcome == "failed"`` AND the artifacts'
post-submit page text contains the Ashby risk-engine signature
("flagged as possible spam"). We grep ``artifacts`` because that's
where ``submitter/phases/verify.py`` stashes the page snippet.

Review-flag rows: any ``review_flags`` row, joined with its parent
Application + Job for context.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from autoapply.tracker.models import Application, Job, ReviewFlag

from api.schemas import ReviewFlagOut, SpamRejectOut
from api.services.apps_service import _to_review_flag_out

SPAM_MARKER = "flagged as possible spam"


def _looks_like_spam_reject(a: Application, j: Job) -> bool:
    """True if this failed app is the Ashby spam-flag pattern.

    The post-submit page text isn't persisted to the DB today — it
    only goes to stdout via ``submitter/phases/verify.py``. The
    distinctive on-disk signature for a spam-flag rejection is:

        outcome="failed"
        AND error_code="no_confirmation"
        AND source="ashby"
        AND final_url is the apply page (not a /submitted variant)

    Plus the legacy fallback of grepping ``error_message`` /
    artifacts for the literal "flagged as possible spam" string in
    case the future code starts persisting the page text.
    """
    if a.outcome != "failed":
        return False

    # Direct text fallback (cheap; matches if anyone ever stores it).
    em = (a.error_message or "").lower()
    artifacts = a.artifacts or {}
    pst = (artifacts.get("post_submit_text") or "").lower()
    if SPAM_MARKER in em or SPAM_MARKER in pst:
        return True

    # Heuristic: ashby + no_confirmation + final_url == apply page.
    # Today every Ashby spam reject lands here (deferred-queue item #4).
    if (
        j.source == "ashby"
        and a.error_code == "no_confirmation"
    ):
        final_url = (artifacts.get("final_url") or "").lower()
        if "/application" in final_url and "/submitted" not in final_url:
            return True

    return False


def list_spam_rejects(s: Session, limit: int = 100) -> list[SpamRejectOut]:
    """Failed applications matching the Ashby risk-engine signature.

    See :func:`_looks_like_spam_reject` for the matcher. At today's
    volume (146 failed rows) this is O(failed). If we ever cross
    ~10k failures we'll persist the marker into a dedicated column.
    """
    rows = s.execute(
        select(Application, Job)
        .join(Job, Job.id == Application.job_id)
        .where(Application.outcome == "failed")
        .order_by(Application.submitted_at.desc())
        .limit(limit * 4)  # over-fetch — most failed rows aren't spam
    ).all()

    out: list[SpamRejectOut] = []
    for a, j in rows:
        if _looks_like_spam_reject(a, j):
            out.append(
                SpamRejectOut(
                    application_id=a.id,
                    job_id=j.id,
                    job_title=j.title,
                    job_company=j.company,
                    job_url=j.url,
                    submitted_at=a.submitted_at,
                    error_code=a.error_code or "",
                    error_message=a.error_message or "",
                )
            )
        if len(out) >= limit:
            break
    return out


def list_review_flags(
    s: Session,
    *,
    reason: list[str] | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[ReviewFlagOut], int]:
    """Paginated flag list, newest first."""
    stmt = select(ReviewFlag)
    if reason:
        stmt = stmt.where(ReviewFlag.reason.in_(reason))

    from sqlalchemy import func

    total = s.execute(
        select(func.count()).select_from(stmt.subquery())
    ).scalar_one()

    stmt = stmt.order_by(ReviewFlag.created_at.desc()).offset(
        (page - 1) * page_size
    ).limit(page_size)
    rows = s.execute(stmt).scalars().all()
    return [_to_review_flag_out(f) for f in rows], int(total)
