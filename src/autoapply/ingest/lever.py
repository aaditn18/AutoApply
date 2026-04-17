"""Lever ingest — public JSON API.

Endpoint: https://api.lever.co/v0/postings/<board_token>?mode=json
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

import httpx

from autoapply.ingest.base import JobSource, RawJob
from autoapply.ingest.greenhouse import _strip_html


log = logging.getLogger(__name__)


LEVER_BASE = "https://api.lever.co/v0/postings"


def _flatten_description(p: dict[str, Any]) -> str:
    """Lever splits the description across `descriptionPlain`, `lists`
    (role-responsibilities / qualifications), and `additional`. Concatenate
    everything — injection scanner and scorer expect a single text blob."""
    parts: list[str] = []
    for k in ("descriptionPlain", "description"):
        v = p.get(k)
        if v:
            parts.append(_strip_html(str(v)) if k == "description" else str(v))
    for item in p.get("lists") or []:
        if not isinstance(item, dict):
            continue
        header = item.get("text") or ""
        content = _strip_html(str(item.get("content") or ""))
        if header and content:
            parts.append(f"{header}\n{content}")
        elif content:
            parts.append(content)
    if p.get("additionalPlain"):
        parts.append(str(p["additionalPlain"]))
    elif p.get("additional"):
        parts.append(_strip_html(str(p["additional"])))
    return "\n\n".join(parts).strip()


def _location(p: dict[str, Any]) -> str:
    cats = p.get("categories") or {}
    if isinstance(cats, dict):
        loc = cats.get("location") or cats.get("allLocations") or ""
        if isinstance(loc, list):
            return ", ".join(str(x) for x in loc if x)
        return str(loc) if loc else ""
    return ""


def _department(p: dict[str, Any]) -> str:
    cats = p.get("categories") or {}
    if not isinstance(cats, dict):
        return ""
    parts = [cats.get("department"), cats.get("team"), cats.get("commitment")]
    return " / ".join(str(p) for p in parts if p)


class LeverSource(JobSource):
    name = "lever"

    def __init__(self, timeout: float = 20.0, user_agent: str = "AutoApply/0.1"):
        self._client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "LeverSource":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def fetch_board(self, board_token: str) -> Iterable[RawJob]:
        url = f"{LEVER_BASE}/{board_token}?mode=json"
        try:
            resp = self._client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("lever fetch failed for %s: %s", board_token, exc)
            return
        data = resp.json()
        if not isinstance(data, list):
            return
        for p in data:
            yield self._to_raw(p, board_token)

    def _to_raw(self, p: dict[str, Any], board_token: str) -> RawJob:
        posting_id = str(p.get("id", ""))
        title = str(p.get("text") or "")
        url = str(p.get("hostedUrl") or p.get("applyUrl") or "")
        desc = _flatten_description(p)
        # Lever doesn't always echo company name; board_token is the slug.
        company = board_token.replace("-", " ").title()
        return RawJob(
            source=self.name,
            source_id=posting_id,
            board_token=board_token,
            url=url,
            title=title,
            company=company,
            location=_location(p),
            department=_department(p),
            description=desc,
            posted_at=str(p.get("createdAt") or ""),
            employment_type=str((p.get("categories") or {}).get("commitment") or ""),
        )
