"""GET + PUT /api/answer_bank, POST /api/answer_bank/preview.

Edits are atomic + backed up. The PUT endpoint validates parse-ability
before writing — a bad YAML edit returns 400 with the parse error so
the UI can surface it inline.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.schemas import (
    AnswerBankPayload,
    AnswerBankPreviewIn,
    AnswerBankPreviewOut,
    AnswerBankWriteIn,
    WriteResult,
)
from api.services.config_writer import (
    read_answer_bank,
    write_answer_bank,
)


router = APIRouter(prefix="/api/answer_bank", tags=["answer_bank"])


@router.get("", response_model=AnswerBankPayload)
def get_answer_bank() -> AnswerBankPayload:
    text, parsed = read_answer_bank()
    return AnswerBankPayload(
        yaml_text=text,
        parsed=parsed,
        keys=sorted(parsed.keys()),
    )


@router.put("", response_model=WriteResult)
def put_answer_bank(body: AnswerBankWriteIn) -> WriteResult:
    try:
        backup = write_answer_bank(body.yaml_text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return WriteResult(
        ok=True,
        message="answer_bank.yml written",
        backup_path=str(backup) if backup else None,
    )


@router.post("/preview", response_model=AnswerBankPreviewOut)
def preview(body: AnswerBankPreviewIn) -> AnswerBankPreviewOut:
    """Resolve a single ``question_type`` against the current bank.

    Lightweight: just looks up the key. Track-aware values are stored
    as ``{track: value}`` dicts in some entries; we honor that.
    """
    _, parsed = read_answer_bank()
    val = parsed.get(body.question_type)
    if val is None:
        return AnswerBankPreviewOut(
            question_type=body.question_type,
            track=body.track,
            value=None,
            found=False,
        )
    if isinstance(val, dict) and body.track:
        v = val.get(body.track) or val.get("_default") or val.get("any")
        return AnswerBankPreviewOut(
            question_type=body.question_type,
            track=body.track,
            value=str(v) if v is not None else None,
            found=v is not None,
        )
    return AnswerBankPreviewOut(
        question_type=body.question_type,
        track=body.track,
        value=str(val),
        found=True,
    )
