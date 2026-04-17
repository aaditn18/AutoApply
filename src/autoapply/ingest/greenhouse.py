"""Greenhouse ingest — public JSON API.

No auth required. Two endpoints per board token:
  https://boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true
    - returns all jobs with description HTML inline
  https://boards-api.greenhouse.io/v1/boards/<token>/jobs/<job_id>
    - single job detail (only needed if the list endpoint was paginated
      or content=true was rate-limited; most boards don't paginate)
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable

import httpx

from autoapply.ingest.base import JobSource, RawJob


log = logging.getLogger(__name__)


GREENHOUSE_BASE = "https://boards-api.greenhouse.io/v1/boards"


def _strip_html(html: str) -> str:
    """Minimal tag + entity strip — good enough for injection scanning and
    keyword matching. We don't care about preserving formatting at this stage."""
    if not html:
        return ""
    # Replace block-level tags with newlines so words don't run together.
    text = re.sub(r"<\s*(?:p|br|li|div|h[1-6]|ul|ol|tr)\b[^>]*>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    # Common entities
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&#39;", "'")
        .replace("&quot;", '"')
    )
    # Collapse excessive whitespace while keeping paragraph breaks.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _first_location(payload: dict[str, Any]) -> str:
    """Greenhouse `location` is a nested dict OR `offices` is a list."""
    loc = payload.get("location")
    if isinstance(loc, dict) and loc.get("name"):
        return str(loc["name"])
    offices = payload.get("offices") or []
    if offices and isinstance(offices, list):
        names = [o.get("name") for o in offices if o and o.get("name")]
        if names:
            return ", ".join(names)
    return ""


def _company_from_metadata(payload: dict[str, Any], board_token: str) -> str:
    """Greenhouse doesn't always echo the company name in the jobs payload —
    fall back to a prettified board token."""
    m = payload.get("metadata") or []
    if isinstance(m, list):
        for entry in m:
            if entry and entry.get("name") == "Company" and entry.get("value"):
                return str(entry["value"])
    return board_token.replace("-", " ").title()


class GreenhouseSource(JobSource):
    name = "greenhouse"

    def __init__(self, timeout: float = 20.0, user_agent: str = "AutoApply/0.1"):
        self._client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "GreenhouseSource":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def fetch_board(self, board_token: str) -> Iterable[RawJob]:
        url = f"{GREENHOUSE_BASE}/{board_token}/jobs?content=true"
        try:
            resp = self._client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("greenhouse fetch failed for %s: %s", board_token, exc)
            return
        data = resp.json()
        jobs = data.get("jobs") or []
        for j in jobs:
            yield self._to_raw(j, board_token)

    def _to_raw(self, j: dict[str, Any], board_token: str) -> RawJob:
        job_id = str(j.get("id", ""))
        title = str(j.get("title") or "")
        url = str(j.get("absolute_url") or "")
        description_html = str(j.get("content") or "")
        description = _strip_html(description_html)
        return RawJob(
            source=self.name,
            source_id=job_id,
            board_token=board_token,
            url=url,
            title=title,
            company=_company_from_metadata(j, board_token),
            location=_first_location(j),
            department=_dept_name(j),
            description=description,
            updated_at=str(j.get("updated_at") or ""),
        )


def _dept_name(payload: dict[str, Any]) -> str:
    depts = payload.get("departments") or []
    if depts and isinstance(depts, list):
        names = [d.get("name") for d in depts if d and d.get("name")]
        if names:
            return " / ".join(names)
    return ""
