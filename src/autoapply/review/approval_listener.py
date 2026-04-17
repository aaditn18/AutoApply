"""Parse `/approve`, `/reject`, `/snooze` comments on review issues.

Invoked from `review-listener.yml` (or from the polled-approvals path
inside `pipeline.yml`). Inputs are the raw issue-comment event payload
from GitHub; outputs are a `Decision` with track override, to be
consumed by the orchestrator.

Security:
- Only the repo owner can approve (`Settings.GH_REPO_OWNER`).
- Commands must START the comment body (no embedding in arbitrary text).
- We refuse to act if the issue doesn't carry our `autoapply:job_canonical_key`
  marker — prevents operating on random issues.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from autoapply.profile.schema import Track


# -- Data structures --------------------------------------------------------


@dataclass
class Decision:
    action: str              # "approve" | "reject" | "snooze" | "ignore"
    track_override: str | None = None
    reason: str = ""


# -- Parsing ----------------------------------------------------------------


_APPROVE_RE = re.compile(r"^\s*/approve(?:\s+(.*))?\s*$", re.IGNORECASE)
_REJECT_RE = re.compile(r"^\s*/reject(?:\s+(.*))?\s*$", re.IGNORECASE)
_SNOOZE_RE = re.compile(r"^\s*/snooze(?:\s+(.*))?\s*$", re.IGNORECASE)
_TRACK_FLAG_RE = re.compile(r"--track[=\s]+(swe|ml|hpc|quant)", re.IGNORECASE)

_CANONICAL_MARKER_RE = re.compile(
    r"<!--\s*autoapply:job_canonical_key=([a-zA-Z0-9_\-]+)\s*-->"
)


def parse_command(comment_body: str) -> Decision:
    """Extract command + track override from a comment body.

    The command MUST be on the first non-empty line. Extra lines
    afterward are ignored (users often paste reasoning below).
    """
    if not comment_body:
        return Decision(action="ignore", reason="empty body")
    first_line = next((ln for ln in comment_body.splitlines() if ln.strip()), "")

    m = _APPROVE_RE.match(first_line)
    if m:
        tail = m.group(1) or ""
        track = _extract_track(tail)
        return Decision(action="approve", track_override=track)

    m = _REJECT_RE.match(first_line)
    if m:
        return Decision(action="reject", reason=(m.group(1) or "").strip())

    m = _SNOOZE_RE.match(first_line)
    if m:
        return Decision(action="snooze", reason=(m.group(1) or "").strip())

    return Decision(action="ignore", reason="no command")


def _extract_track(tail: str) -> str | None:
    m = _TRACK_FLAG_RE.search(tail)
    if not m:
        return None
    t = m.group(1).lower()
    # narrow to Track literal values
    if t in ("swe", "ml", "hpc", "quant"):
        return t
    return None


# -- Issue-body marker extraction ------------------------------------------


def extract_canonical_key(issue_body: str) -> str | None:
    if not issue_body:
        return None
    m = _CANONICAL_MARKER_RE.search(issue_body)
    return m.group(1) if m else None


# -- Authorization ---------------------------------------------------------


def is_authorized(comment_author: str, allowed_actor: str) -> bool:
    """Case-insensitive comparison — GitHub logins aren't case-sensitive."""
    if not comment_author or not allowed_actor:
        return False
    return comment_author.lower() == allowed_actor.lower()


# -- High-level event handler ---------------------------------------------


@dataclass
class HandledEvent:
    """Result of `handle_comment_event()` — fully structured so the
    orchestrator can act deterministically without re-parsing the JSON."""

    decision: Decision
    canonical_key: str | None
    authorized: bool
    comment_author: str
    issue_number: int
    ignored_reason: str = ""


def handle_comment_event(
    event: dict, *, allowed_actor: str
) -> HandledEvent:
    """Parse a GitHub `issue_comment` webhook payload.

    The `event` dict is the JSON GitHub posts to the workflow. Shape
    (abbreviated):
        {
          "action": "created",
          "issue": {"number": 42, "body": "...\n<!-- autoapply:job_canonical_key=abc -->", "state": "open"},
          "comment": {"body": "/approve --track=ml", "user": {"login": "aaditn18"}}
        }
    """
    action = event.get("action", "")
    if action not in ("created", "edited"):
        return HandledEvent(
            decision=Decision(action="ignore"),
            canonical_key=None,
            authorized=False,
            comment_author="",
            issue_number=0,
            ignored_reason=f"action={action}",
        )

    issue = event.get("issue") or {}
    comment = event.get("comment") or {}
    user = comment.get("user") or {}
    author = str(user.get("login") or "")
    body = str(comment.get("body") or "")
    issue_body = str(issue.get("body") or "")
    issue_number = int(issue.get("number") or 0)

    canonical_key = extract_canonical_key(issue_body)
    authorized = is_authorized(author, allowed_actor)
    decision = parse_command(body)

    ignored: str = ""
    if decision.action == "ignore":
        ignored = decision.reason or "no command"
    elif not authorized:
        # Non-owner commands are parsed but not honored — surface the reason.
        ignored = f"unauthorized actor {author!r}"
        decision = Decision(action="ignore", reason=ignored)
    elif canonical_key is None:
        ignored = "issue missing canonical_key marker"
        decision = Decision(action="ignore", reason=ignored)

    return HandledEvent(
        decision=decision,
        canonical_key=canonical_key,
        authorized=authorized,
        comment_author=author,
        issue_number=issue_number,
        ignored_reason=ignored,
    )
