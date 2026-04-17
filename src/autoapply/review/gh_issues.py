"""Post structured review issues to the private AutoApply repo.

One issue per job routed to the review queue. Body is deterministic
Markdown so `approval_listener.py` can parse our own format back.

Auth: fine-grained PAT in `Settings.GH_ISSUES_PAT` with Issues:write on
the AutoApply repo. In CI the workflow's GITHUB_TOKEN is sufficient if
we're running inside the same repo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from autoapply.execute.base import ApplyResult
from autoapply.tracker.models import Job


log = logging.getLogger(__name__)


GH_API_BASE = "https://api.github.com"


# -- Issue rendering --------------------------------------------------------


_ISSUE_TITLE_TMPL = "[{track}] {company} — {title}"


def render_issue_title(job: Job, track: str) -> str:
    t = (track or "?").lower()
    return _ISSUE_TITLE_TMPL.format(
        track=t, company=(job.company or "?")[:80], title=(job.title or "?")[:100]
    )


def render_issue_body(
    job: Job,
    result: ApplyResult,
    *,
    track: str,
    final_rank: float | None = None,
    pay_midpoint: float | None = None,
    loc_signal: float | None = None,
    injection_flagged: bool = False,
) -> str:
    lines: list[str] = []
    lines.append(f"**Company:** {job.company}")
    lines.append(f"**Title:** {job.title}")
    if job.location:
        lines.append(f"**Location:** {job.location}")
    lines.append(f"**Source:** {job.source} / {job.board_token} / {job.source_id}")
    lines.append(f"**URL:** {job.url}")
    lines.append("")
    lines.append("### Scoring")
    lines.append(f"- track: `{track}`")
    if final_rank is not None:
        lines.append(f"- final_rank: `{final_rank:.3f}`")
    if pay_midpoint is not None:
        lines.append(f"- pay_midpoint: `${pay_midpoint:,.0f}`")
    if loc_signal is not None:
        lines.append(f"- loc_signal: `{loc_signal:.2f}`")
    if injection_flagged:
        lines.append("- ⚠️ injection_detected: `true`")
    lines.append("")
    lines.append("### Proposed answers")
    if result.resolved:
        for r in result.resolved:
            # r is dict (JSON-serialized) because we store dry_run artifacts
            # that way; or ResolvedField.  Handle both.
            name = r["name"] if isinstance(r, dict) else r.name
            label = r["label"] if isinstance(r, dict) else r.label
            value = r["value"] if isinstance(r, dict) else r.value
            src = r["source"] if isinstance(r, dict) else r.source
            show_val = value if value else "_<blank>_"
            if len(show_val) > 200:
                show_val = show_val[:197] + "…"
            lines.append(f"- **{label}** ({name}, `{src}`): {show_val}")
    else:
        lines.append("_none_")
    lines.append("")
    if result.unresolved:
        lines.append("### Unresolved fields")
        for u in result.unresolved:
            if isinstance(u, dict):
                lines.append(
                    f"- **{u.get('label')}** ({u.get('name')}): {u.get('reason', '')}"
                )
            else:  # UnresolvedField exception (shouldn't occur; safety)
                lines.append(f"- **{u.label}** ({u.name}): {u.reason}")
        lines.append("")
    if result.review_reasons:
        lines.append("### Why this is in review")
        for reason in result.review_reasons:
            lines.append(f"- `{reason}`")
        lines.append("")
    if result.cover_letter_text:
        lines.append("### Cover letter (draft)")
        lines.append("```")
        # Truncate to ~4k chars to keep issue reasonable.
        cl = result.cover_letter_text
        if len(cl) > 4000:
            cl = cl[:3997] + "..."
        lines.append(cl)
        lines.append("```")
        lines.append("")
    lines.append("### Commands")
    lines.append("Comment one of the following to resolve:")
    lines.append("- `/approve` — submit with the current track")
    lines.append("- `/approve --track=ml` — submit with a different track (`swe`, `ml`, `hpc`, `quant`)")
    lines.append("- `/reject` — discard")
    lines.append("- `/snooze` — move back to the pipeline in 24h")
    lines.append("")
    lines.append(f"<!-- autoapply:job_canonical_key={job.canonical_key} -->")
    return "\n".join(lines)


# -- Issue metadata ---------------------------------------------------------


@dataclass
class IssuePayload:
    title: str
    body: str
    labels: list[str] = field(default_factory=list)


def build_payload(
    job: Job,
    result: ApplyResult,
    *,
    track: str,
    final_rank: float | None = None,
    pay_midpoint: float | None = None,
    loc_signal: float | None = None,
    injection_flagged: bool = False,
) -> IssuePayload:
    labels = ["autoapply:review", f"track:{track}"]
    if injection_flagged:
        labels.append("security:injection-detected")
    if result.cover_letter_text:
        labels.append("has-cover-letter")
    return IssuePayload(
        title=render_issue_title(job, track),
        body=render_issue_body(
            job,
            result,
            track=track,
            final_rank=final_rank,
            pay_midpoint=pay_midpoint,
            loc_signal=loc_signal,
            injection_flagged=injection_flagged,
        ),
        labels=labels,
    )


# -- GitHub client ----------------------------------------------------------


class GitHubIssueClient:
    """Thin wrapper around the REST API. Testable via MockTransport."""

    def __init__(
        self,
        *,
        owner: str,
        repo: str,
        token: str,
        client: httpx.Client | None = None,
        timeout: float = 20.0,
    ):
        if not token:
            raise ValueError("GitHubIssueClient requires a token")
        self.owner = owner
        self.repo = repo
        default_headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "AutoApply/0.1",
        }
        if client is None:
            self._client = httpx.Client(timeout=timeout, headers=default_headers)
        else:
            # Caller-supplied client (tests / shared session): inject auth
            # headers without stomping existing values.
            for k, v in default_headers.items():
                client.headers.setdefault(k, v)
            self._client = client

    def close(self) -> None:
        self._client.close()

    def create_issue(self, payload: IssuePayload) -> dict[str, Any]:
        url = f"{GH_API_BASE}/repos/{self.owner}/{self.repo}/issues"
        resp = self._client.post(
            url,
            json={
                "title": payload.title,
                "body": payload.body,
                "labels": payload.labels,
            },
        )
        resp.raise_for_status()
        return resp.json()

    def close_issue(
        self, issue_number: int, *, comment: str | None = None, state_reason: str = "completed"
    ) -> dict[str, Any]:
        base = f"{GH_API_BASE}/repos/{self.owner}/{self.repo}/issues/{issue_number}"
        if comment:
            r = self._client.post(f"{base}/comments", json={"body": comment})
            r.raise_for_status()
        r = self._client.patch(base, json={"state": "closed", "state_reason": state_reason})
        r.raise_for_status()
        return r.json()

    def add_labels(self, issue_number: int, labels: list[str]) -> None:
        url = f"{GH_API_BASE}/repos/{self.owner}/{self.repo}/issues/{issue_number}/labels"
        resp = self._client.post(url, json={"labels": labels})
        resp.raise_for_status()
