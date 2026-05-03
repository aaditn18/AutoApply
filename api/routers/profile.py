"""GET /api/profile — read-only summary of state/profile.json.

Writes happen via ``autoapply profile-build`` (which rebuilds from
the resume submodule). The Profile tab in the UI shows a summary of
each of the 4 tracks + a "Rebuild" button that POSTs to the
``profile-build`` pipeline endpoint.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from api.schemas import ProfileSnapshot, TrackProfileSummary
from api.services import config_writer


router = APIRouter(prefix="/api/profile", tags=["profile"])


@router.get("", response_model=ProfileSnapshot)
def get_profile() -> ProfileSnapshot:
    p = config_writer.ALLOWED_PATHS["profile"]
    if not p.exists():
        raise HTTPException(status_code=404, detail="profile.json not found")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=500, detail=f"profile.json malformed: {exc}"
        ) from exc

    tracks = []
    for track_name in ("swe", "ml", "hpc", "quant"):
        t = data.get(track_name) or {}
        if not t:
            continue
        tracks.append(
            TrackProfileSummary(
                track=track_name,
                full_name=str(t.get("full_name", "")),
                email=str(t.get("email", "")),
                phone=str(t.get("phone", "")),
                linkedin_url=str(t.get("linkedin_url", "")),
                github_url=str(t.get("github_url", "")),
                skills_count=len(t.get("skills") or []),
                experiences_count=len(t.get("experiences") or []),
                projects_count=len(t.get("projects") or []),
                education_count=len(t.get("education") or []),
            )
        )
    return ProfileSnapshot(tracks=tracks, raw_path=str(p))
