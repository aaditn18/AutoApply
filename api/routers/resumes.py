"""GET /api/resumes — per-track resume metadata."""

from __future__ import annotations

from fastapi import APIRouter

from api.schemas import ResumeOut
from api.services.resumes_service import list_resumes


router = APIRouter(prefix="/api/resumes", tags=["resumes"])


@router.get("", response_model=list[ResumeOut])
def list_resumes_route() -> list[ResumeOut]:
    return list_resumes()
