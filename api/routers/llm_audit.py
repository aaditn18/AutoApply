"""GET /api/llm_audit — list of LLM-call rows across applications."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from api.deps import get_db
from api.schemas import LLMAuditRow
from api.services.llm_audit_service import list_llm_audit


router = APIRouter(prefix="/api/llm_audit", tags=["llm_audit"])


@router.get("", response_model=list[LLMAuditRow])
def list_llm_audit_route(
    limit: int = Query(100, ge=1, le=500),
    s: Session = Depends(get_db),
) -> list[LLMAuditRow]:
    return list_llm_audit(s, limit=limit)
