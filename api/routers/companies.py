"""GET + PUT /api/companies — companies.yml grid editor.

Round-trips through ``ruamel.yaml`` so the philosophy comments at
the top of the file stay intact across edits. Validation enforces
known sources / tiers and cleans token format (no whitespace,
no slashes).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.schemas import CompaniesPayload, CompaniesWriteIn, WriteResult
from api.services.config_writer import read_companies, write_companies


router = APIRouter(prefix="/api/companies", tags=["companies"])


@router.get("", response_model=CompaniesPayload)
def get_companies() -> CompaniesPayload:
    return CompaniesPayload(sources=read_companies())


@router.put("", response_model=WriteResult)
def put_companies(body: CompaniesWriteIn) -> WriteResult:
    try:
        backup = write_companies(body.sources)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return WriteResult(
        ok=True,
        message="companies.yml written",
        backup_path=str(backup) if backup else None,
    )
