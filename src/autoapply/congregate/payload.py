"""Merge resolved answers + cover-letter text into a submit-ready payload.

This sits between the execute layer and whoever actually sends the
HTTP request (Playwright or the fast-path submitters). The Applicator
subclasses build their own raw payload in `build_payload()`; this
module is a thin wrapper that normalizes + strips secrets before we
persist or log.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# Keys whose values are never logged or committed.
_SECRET_KEYS = {
    "password",
    "token",
    "auth_token",
    "api_key",
    "session_cookie",
    "storage_state",
}


@dataclass
class SubmitPayload:
    data: dict[str, str] = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)  # field_name → path
    track_submitted: str = ""
    cover_letter_text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def build_submit_payload(
    *,
    applicator_payload: dict[str, Any],
    track: str,
    cover_letter_text: str = "",
    resume_path: str | None = None,
    extra: dict[str, Any] | None = None,
) -> SubmitPayload:
    """Collapse the applicator's `{data, files, ...}` dict into SubmitPayload.

    Applicators that don't return both `data` and `files` (e.g., a future
    Playwright path emitting raw selectors) can pass their flat dict via
    `extra`; everything else is best-effort mapped.
    """
    data = dict(applicator_payload.get("data") or {})
    files = dict(applicator_payload.get("files") or {})
    if resume_path and "resume" not in files:
        files["resume"] = resume_path
    if cover_letter_text and "cover_letter" not in data and "cover_letter" not in files:
        data["cover_letter"] = cover_letter_text
    if extra:
        for k, v in extra.items():
            if isinstance(v, str):
                data.setdefault(k, v)
    return SubmitPayload(
        data=_scrub(data),
        files=_scrub_paths(files),
        track_submitted=track,
        cover_letter_text=cover_letter_text,
        metadata={
            k: v
            for k, v in applicator_payload.items()
            if k not in ("data", "files")
        },
    )


def _scrub(d: dict[str, Any]) -> dict[str, str]:
    return {
        k: str(v) if v is not None else ""
        for k, v in d.items()
        if k.lower() not in _SECRET_KEYS
    }


def _scrub_paths(d: dict[str, Any]) -> dict[str, str]:
    # Keep paths but ensure they're strings; never redact a path because
    # the applicator needs it to actually attach the file.
    return {k: str(v) for k, v in d.items() if v not in (None, "")}


def to_log_dict(payload: SubmitPayload) -> dict[str, Any]:
    """Render payload for a log line / audit entry. Truncates long fields."""
    data_preview = {}
    for k, v in payload.data.items():
        if isinstance(v, str) and len(v) > 120:
            data_preview[k] = v[:117] + "…"
        else:
            data_preview[k] = v
    return {
        "track": payload.track_submitted,
        "fields": data_preview,
        "files": payload.files,
        "cover_letter_chars": len(payload.cover_letter_text),
    }
