"""Ashby ingest — public unauthenticated job-board API.

Endpoint: https://api.ashbyhq.com/posting-api/job-board/<board_token>
         ?includeCompensation=true

Returns a JSON object with ``apiVersion`` and ``jobs`` (list of posting
dicts). No auth required.

Ashby also exposes an authenticated Developer API (``jobPosting.info``
with ``applicationFormDefinition``, ``applicationForm.submit``,
``file.createFileUploadHandle``) but those require a per-customer API
key with ``jobsRead`` / ``candidatesWrite`` scopes — not usable for
a cross-company portal. Form discovery + submission for Ashby happen
via browser automation against the hosted apply SPA at
``jobs.ashbyhq.com/<company>/<uuid>/application``, driven by the
Stage-2 DOM batch in ``execute/submitter/dom/``.

Rate limit note: Ashby's unofficial limit is ~100 req/min on this
endpoint. Our existing HTTP-call jitter (~100–400 ms) keeps us well
under that; no dedicated throttle needed here.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Iterable

import httpx

from autoapply.ingest.base import JobSource, RawJob
from autoapply.ingest.greenhouse import _strip_html


log = logging.getLogger(__name__)


ASHBY_BASE = "https://api.ashbyhq.com/posting-api/job-board"


def _description(p: dict[str, Any]) -> str:
    """Prefer Ashby's plain-text variant; fall back to stripped HTML.

    Ashby consistently ships both ``descriptionPlain`` and
    ``descriptionHtml``. We use plain when available and strip HTML as
    the backup — matches the "description is plain text downstream"
    invariant the scorer + injection scanner rely on.
    """
    plain = p.get("descriptionPlain")
    if plain:
        return str(plain).strip()
    html = p.get("descriptionHtml")
    if html:
        return _strip_html(str(html))
    return ""


def _location(p: dict[str, Any]) -> str:
    """Primary location string — keep unchanged, downstream filters
    already handle 'Remote - US', ', MD', 'Remote, Bulgaria' variants.

    ``secondaryLocations`` go into metadata for reference but don't
    influence the primary location field (scoring reads that one).
    """
    loc = p.get("location")
    return str(loc).strip() if loc else ""


def _department(p: dict[str, Any]) -> str:
    """Concatenate Ashby's department + team (both optional)."""
    parts = [p.get("department"), p.get("team")]
    return " / ".join(str(x) for x in parts if x)


def _metadata(p: dict[str, Any]) -> dict[str, str]:
    """Flatten Ashby-specific fields into the str-only metadata dict.

    ``RawJob.metadata`` is ``dict[str, str]`` so we stringify anything
    nested. Useful fields preserved: remote-eligibility, workplace
    type, secondary locations joined, compensation summary, apply URL.
    """
    md: dict[str, str] = {}
    if (rem := p.get("isRemote")) is not None:
        md["isRemote"] = "true" if rem else "false"
    if (wp := p.get("workplaceType")):
        md["workplaceType"] = str(wp)
    if (emp := p.get("employmentType")):
        md["employmentType"] = str(emp)
    secondary = p.get("secondaryLocations") or []
    if isinstance(secondary, list) and secondary:
        names = []
        for s in secondary:
            if isinstance(s, dict):
                nm = s.get("location") or s.get("name")
                if nm:
                    names.append(str(nm))
        if names:
            md["secondaryLocations"] = ", ".join(names)
    # Ashby's optional compensation block — stringify just enough for
    # audit trails. The pay_extractor will still mine the JD separately;
    # this is a convenience pointer for debugging.
    comp = p.get("compensation")
    if isinstance(comp, dict) and comp.get("compensationTierSummary"):
        md["compensationSummary"] = str(comp["compensationTierSummary"])
    if (aurl := p.get("applyUrl")):
        md["applyUrl"] = str(aurl)
    return md


class AshbySource(JobSource):
    """Fetches published postings from Ashby's public job-board feed."""

    name = "ashby"

    def __init__(
        self,
        timeout: float = 20.0,
        user_agent: str = "AutoApply/0.1",
        client: httpx.Client | None = None,
    ):
        # ``client`` is injected by tests via ``httpx.MockTransport``.
        # Production path uses plain httpx (unlike Lever, Ashby's public
        # API doesn't JA3-fingerprint so curl_cffi isn't needed here).
        self._client = client or httpx.Client(
            timeout=timeout,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "AshbySource":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def fetch_board(self, board_token: str) -> Iterable[RawJob]:
        url = f"{ASHBY_BASE}/{board_token}?includeCompensation=true"
        try:
            resp = self._client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("ashby fetch failed for %s: %s", board_token, exc)
            return
        data = resp.json()
        jobs = data.get("jobs") if isinstance(data, dict) else None
        if not isinstance(jobs, list):
            return
        for p in jobs:
            # Ashby exposes draft + unlisted postings with ``isListed=False``;
            # skip them — applying to a draft role is silly + may fail.
            if isinstance(p, dict) and p.get("isListed") is False:
                continue
            yield self._to_raw(p, board_token)

    def _to_raw(self, p: dict[str, Any], board_token: str) -> RawJob:
        # Ashby doesn't always include a stable ``id`` in the public
        # feed — fall back to a hash of jobUrl so dedup keys stay
        # stable across ingest runs.
        posting_id = str(p.get("id") or "").strip()
        if not posting_id:
            job_url_for_hash = str(p.get("jobUrl") or p.get("applyUrl") or "")
            posting_id = hashlib.sha256(job_url_for_hash.encode()).hexdigest()[:16]
        title = str(p.get("title") or "")
        # Prefer applyUrl (candidate-facing) over jobUrl (read-only listing).
        url = str(p.get("applyUrl") or p.get("jobUrl") or "")
        # Ashby's public feed doesn't include a per-posting company name;
        # canonicalize the board token the same way Lever does.
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
            description=_description(p),
            posted_at=str(p.get("publishedAt") or ""),
            employment_type=str(p.get("employmentType") or ""),
            metadata=_metadata(p),
        )
