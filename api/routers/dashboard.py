"""GET /api/dashboard — KPI tiles for the home page."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.deps import get_db
from api.schemas import DashboardOut
from api.services.dashboard_service import build_dashboard


router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardOut)
def get_dashboard_route(s: Session = Depends(get_db)) -> DashboardOut:
    return build_dashboard(s)
