"""Review queue: GET /api/review/spam + GET /api/review/flags + write actions."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from autoapply.tracker.models import Application, Job, ReviewFlag

from api.deps import get_db
from api.schemas import (
    FlagResolveIn,
    FlagResolveOut,
    Page,
    SpamRejectOut,
    WriteResult,
)
from api.services import pipeline_runner
from api.services.config_writer import read_answer_bank, write_answer_bank
from api.services.review_service import list_review_flags, list_spam_rejects


router = APIRouter(prefix="/api/review", tags=["review"])


@router.get("/spam", response_model=list[SpamRejectOut])
def list_spam_route(
    limit: int = Query(100, ge=1, le=500),
    s: Session = Depends(get_db),
) -> list[SpamRejectOut]:
    return list_spam_rejects(s, limit=limit)


@router.get("/flags", response_model=Page)
def list_flags_route(
    reason: list[str] | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    s: Session = Depends(get_db),
) -> Page:
    items, total = list_review_flags(
        s, reason=reason, page=page, page_size=page_size,
    )
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("/flags/{flag_id}/resolve", response_model=FlagResolveOut)
async def resolve_flag(
    flag_id: int,
    body: FlagResolveIn,
    s: Session = Depends(get_db),
) -> FlagResolveOut:
    """Resolve a review flag by writing the answer to ``answer_bank.yml``.

    The bank key is either:
      - ``body.bank_key`` if the caller provided one (e.g. a known
        ``QuestionType`` slug),
      - ``review_flag.question_type`` if it was classified,
      - or a slugified field_label as a last resort.

    On success: writes the YAML, deletes the flag row, and queues a
    retry of the Application via ``apply-by-ids``.
    """
    flag = s.get(ReviewFlag, flag_id)
    if flag is None:
        raise HTTPException(status_code=404, detail=f"flag {flag_id} not found")

    bank_key = (
        body.bank_key
        or flag.question_type
        or _slugify(flag.field_label)
    )
    if not bank_key:
        raise HTTPException(
            status_code=400,
            detail="cannot derive a bank key — provide body.bank_key",
        )

    text, parsed = read_answer_bank()
    parsed[bank_key] = body.value
    new_text = _serialize_yaml_preserving(text, bank_key, body.value)
    try:
        write_answer_bank(new_text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Drop the flag and queue a retry
    job_id = flag.job_id
    s.delete(flag)
    s.commit()

    run_id = await pipeline_runner.start_run(
        "apply-by-ids",
        job_ids=[job_id],
        dry_run=False,  # user-initiated → assume real submit
    )

    return FlagResolveOut(
        ok=True,
        bank_key_written=bank_key,
        retry_run_id=run_id,
    )


@router.post("/spam/{app_id}/archive", response_model=WriteResult)
def archive_spam(
    app_id: int,
    s: Session = Depends(get_db),
) -> WriteResult:
    """Move a spam-flagged Job to ``archived`` status (skip future passes)."""
    a = s.get(Application, app_id)
    if a is None:
        raise HTTPException(status_code=404, detail=f"app {app_id} not found")
    j = s.get(Job, a.job_id)
    if j is None:
        raise HTTPException(status_code=404, detail=f"job {a.job_id} not found")
    j.status = "archived"
    s.commit()
    return WriteResult(ok=True, message=f"job {j.id} archived")


# ── helpers ─────────────────────────────────────────────────────────


def _slugify(s: str) -> str:
    out = []
    for ch in (s or "").lower():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "_":
            out.append("_")
    return "".join(out).strip("_")[:64]


def _serialize_yaml_preserving(yaml_text: str, key: str, value: str) -> str:
    """Append-or-replace ``key: value`` while preserving the rest of the file.

    For now we use a simple approach: parse via ruamel, set the key,
    dump. Comments survive thanks to round-trip mode. Multiline values
    fall back to plain quoting.
    """
    from io import StringIO
    from ruamel.yaml import YAML

    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.width = 120
    parsed = yaml.load(yaml_text) or {}
    parsed[key] = value
    buf = StringIO()
    yaml.dump(parsed, buf)
    return buf.getvalue()
