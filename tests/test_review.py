"""Review layer — issue rendering + comment parsing."""

from __future__ import annotations

import json
from dataclasses import asdict

import httpx
import pytest

from autoapply.execute.base import ApplyResult
from autoapply.review.approval_listener import (
    Decision,
    extract_canonical_key,
    handle_comment_event,
    is_authorized,
    parse_command,
)
from autoapply.review.gh_issues import (
    GitHubIssueClient,
    IssuePayload,
    build_payload,
    render_issue_body,
    render_issue_title,
)
from autoapply.tracker.models import Job


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _job() -> Job:
    return Job(
        canonical_key="abc1234",
        source="greenhouse",
        source_id="12345",
        board_token="acme",
        url="https://boards.greenhouse.io/acme/jobs/12345",
        title="Software Engineer",
        company="Acme",
        location="New York, NY",
    )


def _dry_run_result() -> ApplyResult:
    return ApplyResult(
        outcome="review",
        answers={"first_name": "Aadit", "email": "aaditnilay@gmail.com"},
        resolved=[
            {
                "name": "first_name",
                "label": "First Name",
                "value": "Aadit",
                "source": "machine_key",
                "question_type": None,
                "requires_llm": False,
                "requires_review": False,
            },
            {
                "name": "question_why",
                "label": "Why do you want to work here?",
                "value": "",
                "source": "llm_required",
                "question_type": "why_company",
                "requires_llm": True,
                "requires_review": False,
            },
        ],
        unresolved=[],
        review_reasons=["llm:question_why"],
    )


# ---------------------------------------------------------------------------
# Issue rendering
# ---------------------------------------------------------------------------


def test_render_issue_title():
    assert render_issue_title(_job(), "swe") == "[swe] Acme — Software Engineer"


def test_render_issue_body_contains_fields():
    body = render_issue_body(
        _job(),
        _dry_run_result(),
        track="swe",
        final_rank=0.85,
        pay_midpoint=180_000,
        loc_signal=0.15,
    )
    assert "Acme" in body
    assert "Software Engineer" in body
    assert "final_rank" in body and "0.850" in body
    assert "$180,000" in body
    assert "loc_signal" in body
    # Answers rendered.
    assert "First Name" in body
    assert "Why do you want to work here?" in body
    # Review reasons section.
    assert "Why this is in review" in body
    assert "llm:question_why" in body
    # Command cheat-sheet.
    assert "/approve" in body
    assert "/approve --track=ml" in body
    assert "/reject" in body
    # Canonical marker embedded.
    assert "autoapply:job_canonical_key=abc1234" in body


def test_render_issue_body_with_injection_flag():
    body = render_issue_body(
        _job(),
        _dry_run_result(),
        track="quant",
        injection_flagged=True,
    )
    assert "injection_detected" in body
    assert "⚠️" in body


def test_render_issue_body_with_cover_letter():
    result = _dry_run_result()
    result.cover_letter_text = "Dear hiring manager,\n\nThank you..."
    body = render_issue_body(_job(), result, track="swe")
    assert "Cover letter" in body
    assert "Dear hiring manager" in body


def test_render_issue_body_truncates_long_cover_letter():
    result = _dry_run_result()
    result.cover_letter_text = "x" * 5000
    body = render_issue_body(_job(), result, track="swe")
    # Truncation leaves ~4k chars + ellipsis.
    assert "..." in body
    assert "x" * 5000 not in body


def test_build_payload_labels_include_track():
    payload = build_payload(_job(), _dry_run_result(), track="ml")
    assert "autoapply:review" in payload.labels
    assert "track:ml" in payload.labels


def test_build_payload_labels_flag_injection():
    payload = build_payload(_job(), _dry_run_result(), track="swe", injection_flagged=True)
    assert "security:injection-detected" in payload.labels


def test_build_payload_labels_has_cover_letter_when_present():
    result = _dry_run_result()
    result.cover_letter_text = "..."
    payload = build_payload(_job(), result, track="swe")
    assert "has-cover-letter" in payload.labels


# ---------------------------------------------------------------------------
# GitHub client (MockTransport)
# ---------------------------------------------------------------------------


def _mock_gh_transport(status_code: int, payload) -> httpx.MockTransport:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["last_url"] = str(request.url)
        captured["last_method"] = request.method
        captured["last_body"] = request.content.decode() if request.content else ""
        captured["last_auth"] = request.headers.get("Authorization", "")
        return httpx.Response(status_code, json=payload)

    transport = httpx.MockTransport(handler)
    transport.captured = captured  # type: ignore[attr-defined]
    return transport


def test_gh_client_requires_token():
    with pytest.raises(ValueError):
        GitHubIssueClient(owner="aaditn18", repo="AutoApply", token="")


def test_gh_client_create_issue_posts_payload():
    transport = _mock_gh_transport(201, {"number": 42, "html_url": "..."})
    client = httpx.Client(transport=transport)
    gh = GitHubIssueClient(
        owner="aaditn18", repo="AutoApply", token="test-token", client=client
    )
    payload = IssuePayload(title="T", body="B", labels=["l1"])
    out = gh.create_issue(payload)
    assert out["number"] == 42
    assert transport.captured["last_method"] == "POST"
    assert "/repos/aaditn18/AutoApply/issues" in transport.captured["last_url"]
    assert "Bearer test-token" in transport.captured["last_auth"]
    body = json.loads(transport.captured["last_body"])
    assert body["title"] == "T"
    assert body["labels"] == ["l1"]


def test_gh_client_close_issue_posts_comment_then_closes():
    seq: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seq.append((request.method, str(request.url)))
        return httpx.Response(200, json={"number": 42})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    gh = GitHubIssueClient(
        owner="aaditn18", repo="AutoApply", token="tok", client=client
    )
    gh.close_issue(42, comment="approved")
    assert len(seq) == 2
    assert seq[0][0] == "POST" and seq[0][1].endswith("/issues/42/comments")
    assert seq[1][0] == "PATCH" and seq[1][1].endswith("/issues/42")


# ---------------------------------------------------------------------------
# Comment parsing
# ---------------------------------------------------------------------------


def test_parse_approve_bare():
    d = parse_command("/approve")
    assert d.action == "approve"
    assert d.track_override is None


def test_parse_approve_with_track():
    d = parse_command("/approve --track=ml")
    assert d.action == "approve"
    assert d.track_override == "ml"


def test_parse_approve_with_track_space_separated():
    d = parse_command("/approve --track ml")
    assert d.action == "approve"
    assert d.track_override == "ml"


def test_parse_approve_with_invalid_track():
    d = parse_command("/approve --track=neural")
    # Invalid track flag → approve, but no override applied.
    assert d.action == "approve"
    assert d.track_override is None


def test_parse_reject_with_reason():
    d = parse_command("/reject not US-based")
    assert d.action == "reject"
    assert "US-based" in d.reason


def test_parse_snooze():
    d = parse_command("/snooze")
    assert d.action == "snooze"


def test_parse_ignores_chat_in_middle():
    d = parse_command("Looks great!\n/approve")
    # Command must be on the first non-empty line; chat first → ignored.
    assert d.action == "ignore"


def test_parse_honors_first_non_empty_line():
    d = parse_command("\n\n/approve --track=hpc\n\nlgtm")
    assert d.action == "approve"
    assert d.track_override == "hpc"


def test_parse_ignore_on_empty():
    assert parse_command("").action == "ignore"
    assert parse_command("   \n\n").action == "ignore"


def test_parse_ignore_on_non_command():
    assert parse_command("nice work team").action == "ignore"


# ---------------------------------------------------------------------------
# Canonical-key extraction
# ---------------------------------------------------------------------------


def test_extract_canonical_key_happy():
    body = "...\n<!-- autoapply:job_canonical_key=abc1234 -->\n"
    assert extract_canonical_key(body) == "abc1234"


def test_extract_canonical_key_missing():
    assert extract_canonical_key("no marker here") is None
    assert extract_canonical_key("") is None


# ---------------------------------------------------------------------------
# is_authorized
# ---------------------------------------------------------------------------


def test_is_authorized_case_insensitive():
    assert is_authorized("AADITN18", "aaditn18") is True


def test_is_authorized_rejects_others():
    assert is_authorized("randobot", "aaditn18") is False
    assert is_authorized("", "aaditn18") is False


# ---------------------------------------------------------------------------
# handle_comment_event
# ---------------------------------------------------------------------------


def _event(body: str, *, author: str = "aaditn18", issue_body: str | None = None):
    if issue_body is None:
        issue_body = "Review for job\n<!-- autoapply:job_canonical_key=abc1234 -->"
    return {
        "action": "created",
        "issue": {"number": 42, "body": issue_body, "state": "open"},
        "comment": {"body": body, "user": {"login": author}},
    }


def test_handle_event_happy_approve():
    out = handle_comment_event(_event("/approve"), allowed_actor="aaditn18")
    assert out.decision.action == "approve"
    assert out.canonical_key == "abc1234"
    assert out.authorized is True
    assert out.issue_number == 42


def test_handle_event_approve_with_track_override():
    out = handle_comment_event(_event("/approve --track=quant"), allowed_actor="aaditn18")
    assert out.decision.action == "approve"
    assert out.decision.track_override == "quant"


def test_handle_event_unauthorized_actor_is_ignored():
    out = handle_comment_event(
        _event("/approve", author="randobot"), allowed_actor="aaditn18"
    )
    assert out.decision.action == "ignore"
    assert "unauthorized" in out.ignored_reason


def test_handle_event_missing_canonical_key_is_ignored():
    out = handle_comment_event(
        _event("/approve", issue_body="no marker"), allowed_actor="aaditn18"
    )
    assert out.decision.action == "ignore"
    assert "canonical_key" in out.ignored_reason


def test_handle_event_non_command_is_ignored_before_auth_check():
    # A chat message from the owner shouldn't even look at auth — just ignore.
    out = handle_comment_event(_event("lgtm!"), allowed_actor="aaditn18")
    assert out.decision.action == "ignore"


def test_handle_event_wrong_action_is_ignored():
    ev = _event("/approve")
    ev["action"] = "deleted"
    out = handle_comment_event(ev, allowed_actor="aaditn18")
    assert out.decision.action == "ignore"
    assert "deleted" in out.ignored_reason


def test_handle_event_reject_propagates_reason():
    out = handle_comment_event(
        _event("/reject not a good fit"), allowed_actor="aaditn18"
    )
    assert out.decision.action == "reject"
    assert "not a good fit" in out.decision.reason
