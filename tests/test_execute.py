"""Execute-layer tests.

Covers:
    - standard_fields.resolve_field / resolve_all — machine-key, classifier,
      required-unresolved exception paths
    - Greenhouse + Lever form parsing via MockTransport
    - DRY_RUN payload dumping + review routing
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from autoapply.answers.bank import AnswerBank
from autoapply.answers.types import QuestionType
from autoapply.execute.base import Applicator, ApplyResult
from autoapply.execute.greenhouse_apply import GreenhouseApplicator
from autoapply.execute.lever_apply import LeverApplicator
from autoapply.execute.standard_fields import (
    FieldSpec,
    ResolvedField,
    UnresolvedField,
    resolve_all,
    resolve_field,
)
from autoapply.profile.schema import (
    DateRange,
    Education,
    Experience,
    Profile,
    Skills,
)
from autoapply.tracker.models import Job


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _profile() -> Profile:
    return Profile(
        track="swe",
        full_name="Aadit Nilay",
        email="aaditnilay@gmail.com",
        phone="+1-240-555-0100",
        linkedin_url="https://www.linkedin.com/in/aadit-nilay/",
        github_url="https://github.com/aaditn18",
        education=[
            Education(
                school="University of Maryland, College Park",
                location="College Park, MD",
                degree="B.S. Computer Science and Mathematics",
                date_range=DateRange(
                    raw="Aug 2022 -- May 2026",
                    start=date(2022, 8, 1),
                    end=date(2026, 5, 1),
                ),
                gpa="3.975",
            )
        ],
        experiences=[
            Experience(
                title="SWE Intern",
                company="PayPal",
                stack=["Python"],
                date_range=DateRange(
                    raw="May 2025 -- Aug 2025",
                    start=date(2025, 5, 1),
                    end=date(2025, 8, 1),
                ),
            )
        ],
        skills=Skills(languages=["Python", "C++"]),
        years_of_experience={"Python": 3.0, "C++": 1.0},
    )


@pytest.fixture()
def profile():
    return _profile()


@pytest.fixture()
def bank():
    return AnswerBank.from_path(
        Path(__file__).resolve().parents[1] / "state" / "answer_bank.yml"
    )


def _job(source="greenhouse", token="acme", sid="12345", title="Software Engineer", url="https://boards.greenhouse.io/acme/jobs/12345") -> Job:
    j = Job(
        canonical_key="abc" + sid,
        source=source,
        source_id=sid,
        board_token=token,
        url=url,
        title=title,
        company="Acme",
    )
    j.id = 1  # simulate a persisted row so _dump_dry_run keys work
    return j


def _mock_transport(status_code: int, payload) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload)

    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# standard_fields.resolve_field
# ---------------------------------------------------------------------------


def test_machine_key_first_name(profile, bank):
    spec = FieldSpec(name="first_name", label="First Name", required=True)
    r = resolve_field(spec, profile=profile, bank=bank, track="swe")
    assert r.value == "Aadit"
    assert r.source == "machine_key"


def test_machine_key_last_name(profile, bank):
    spec = FieldSpec(name="last_name", label="Last Name", required=True)
    r = resolve_field(spec, profile=profile, bank=bank, track="swe")
    assert r.value == "Nilay"


def test_machine_key_email(profile, bank):
    spec = FieldSpec(name="email", label="Email", required=True)
    r = resolve_field(spec, profile=profile, bank=bank, track="swe")
    assert r.value == "aaditnilay@gmail.com"


def test_machine_key_linkedin(profile, bank):
    spec = FieldSpec(name="urls[linkedin]", label="LinkedIn", required=False)
    r = resolve_field(spec, profile=profile, bank=bank, track="swe")
    assert "linkedin.com/in/aadit-nilay" in r.value


def test_machine_key_github(profile, bank):
    spec = FieldSpec(name="urls[github]", label="GitHub", required=False)
    r = resolve_field(spec, profile=profile, bank=bank, track="swe")
    assert "github.com/aaditn18" in r.value


def test_resume_file_field_uses_injected_path(profile, bank, tmp_path):
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4\n")
    spec = FieldSpec(name="resume", label="Resume", required=True, kind="file")
    r = resolve_field(
        spec, profile=profile, bank=bank, track="swe", resume_path=str(resume)
    )
    assert r.value == str(resume)
    assert r.source == "machine_key"


def test_resume_missing_and_required_raises(profile, bank):
    spec = FieldSpec(name="resume", label="Resume", required=True, kind="file")
    with pytest.raises(UnresolvedField):
        resolve_field(spec, profile=profile, bank=bank, track="swe")


def test_cover_letter_field_uses_provided_text(profile, bank):
    spec = FieldSpec(
        name="cover_letter", label="Cover Letter", required=False, kind="file"
    )
    r = resolve_field(
        spec,
        profile=profile,
        bank=bank,
        track="swe",
        cover_letter_text="Dear Acme...",
    )
    assert r.value == "Dear Acme..."


def test_classifier_routes_work_auth(profile, bank):
    spec = FieldSpec(
        name="question_1",
        label="Are you legally authorized to work in the United States?",
        required=True,
    )
    r = resolve_field(spec, profile=profile, bank=bank, track="swe")
    assert r.value.lower().startswith("yes")
    assert r.source in ("classifier+bank", "profile")


def test_classifier_routes_gpa_to_profile(profile, bank):
    spec = FieldSpec(name="question_2", label="What is your GPA?", required=False)
    r = resolve_field(spec, profile=profile, bank=bank, track="swe")
    assert r.value == "3.975"
    assert r.source == "profile"


def test_classifier_snaps_yes_no_to_option(profile, bank):
    spec = FieldSpec(
        name="question_5",
        label="Will you now or in the future require visa sponsorship?",
        required=True,
        kind="select",
        options=["Yes", "No"],
    )
    r = resolve_field(spec, profile=profile, bank=bank, track="swe")
    # F-1/OPT student will require future H-1B sponsorship → "Yes"
    assert r.value == "Yes"


def test_llm_required_field_routes_to_review(profile, bank):
    # WHY_COMPANY is LLM_REQUIRED — bank returns requires_llm=True.
    spec = FieldSpec(
        name="question_7",
        label="Why do you want to work at our company?",
        required=True,
        kind="textarea",
    )
    r = resolve_field(spec, profile=profile, bank=bank, track="swe")
    assert r.requires_llm is True
    assert r.source == "llm_required"


def test_unknown_required_field_raises(profile, bank):
    # Completely novel label → classifier returns UNKNOWN → required raises.
    spec = FieldSpec(
        name="question_42",
        label="Describe the color of your favorite parrot.",
        required=True,
    )
    with pytest.raises(UnresolvedField):
        resolve_field(spec, profile=profile, bank=bank, track="swe")


def test_unknown_optional_field_returns_empty(profile, bank):
    spec = FieldSpec(
        name="question_42", label="Describe the color of your favorite parrot.",
        required=False,
    )
    r = resolve_field(spec, profile=profile, bank=bank, track="swe")
    assert r.value == ""
    assert r.source == "none"


def test_resolve_all_collects_resolved_and_unresolved(profile, bank):
    specs = [
        FieldSpec(name="first_name", label="First Name", required=True),
        FieldSpec(name="email", label="Email", required=True),
        FieldSpec(
            name="question_99", label="Describe your favorite kind of tuna.",
            required=True,
        ),
    ]
    resolved, unresolved = resolve_all(
        specs, profile=profile, bank=bank, track="swe"
    )
    assert len(resolved) == 2
    assert len(unresolved) == 1
    assert unresolved[0].name == "question_99"


# ---------------------------------------------------------------------------
# Greenhouse applicator
# ---------------------------------------------------------------------------


_GH_FORM_PAYLOAD = {
    "id": 12345,
    "title": "Software Engineer",
    "questions": [
        {
            "label": "First Name",
            "required": True,
            "fields": [{"name": "first_name", "type": "input_text"}],
        },
        {
            "label": "Last Name",
            "required": True,
            "fields": [{"name": "last_name", "type": "input_text"}],
        },
        {
            "label": "Email",
            "required": True,
            "fields": [{"name": "email", "type": "input_text"}],
        },
        {
            "label": "Resume",
            "required": True,
            "fields": [{"name": "resume", "type": "input_file"}],
        },
        {
            "label": "Are you legally authorized to work in the United States?",
            "required": True,
            "fields": [
                {
                    "name": "question_auth",
                    "type": "multi_value_single_select_fields",
                    "values": [
                        {"value": 1, "label": "Yes"},
                        {"value": 0, "label": "No"},
                    ],
                }
            ],
        },
    ],
}


def test_greenhouse_fetch_form_parses_questions(profile, bank, tmp_path):
    client = httpx.Client(transport=_mock_transport(200, _GH_FORM_PAYLOAD))
    app = GreenhouseApplicator(
        client=client,
        profile=profile,
        bank=bank,
        track="swe",
        resume_path=str(tmp_path / "resume.pdf"),
        dry_runs_dir=tmp_path / "dry",
    )
    specs = app.fetch_form(_job())
    by_name = {s.name: s for s in specs}
    assert "first_name" in by_name
    assert "email" in by_name
    assert by_name["resume"].kind == "file"
    assert by_name["question_auth"].kind == "select"
    assert "Yes" in by_name["question_auth"].options


def test_greenhouse_apply_dry_run_dumps_payload(profile, bank, tmp_path):
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4\n")
    client = httpx.Client(transport=_mock_transport(200, _GH_FORM_PAYLOAD))
    app = GreenhouseApplicator(
        client=client,
        profile=profile,
        bank=bank,
        track="swe",
        resume_path=str(resume),
        dry_runs_dir=tmp_path / "dry",
    )
    result = app.apply(_job(), dry_run=True)
    assert result.outcome == "dry_run", result
    # Artifact dumped.
    dump_files = list((tmp_path / "dry").glob("*.json"))
    assert len(dump_files) == 1
    body = json.loads(dump_files[0].read_text())
    assert body["applicator"] == "greenhouse"
    # Sanity — answers include first_name and work-auth.
    names = {r["name"] for r in body["resolved"]}
    assert "first_name" in names
    assert "question_auth" in names


def test_greenhouse_apply_routes_to_review_on_unknown_required(profile, bank, tmp_path):
    payload = {
        "id": 12345,
        "title": "Software Engineer",
        "questions": [
            {
                "label": "First Name",
                "required": True,
                "fields": [{"name": "first_name", "type": "input_text"}],
            },
            {
                "label": "Describe your favorite kind of tuna.",
                "required": True,
                "fields": [{"name": "question_tuna", "type": "input_text"}],
            },
        ],
    }
    client = httpx.Client(transport=_mock_transport(200, payload))
    app = GreenhouseApplicator(
        client=client, profile=profile, bank=bank, track="swe",
        dry_runs_dir=tmp_path / "dry",
    )
    result = app.apply(_job(), dry_run=True)
    assert result.outcome == "review"
    assert any(r.startswith("unresolved:question_tuna") for r in result.review_reasons)


def test_greenhouse_apply_routes_to_review_on_llm_required(profile, bank, tmp_path):
    payload = {
        "id": 12345,
        "title": "Software Engineer",
        "questions": [
            {
                "label": "First Name",
                "required": True,
                "fields": [{"name": "first_name", "type": "input_text"}],
            },
            {
                "label": "Why do you want to work at our company?",
                "required": True,
                "fields": [{"name": "question_why", "type": "textarea"}],
            },
        ],
    }
    client = httpx.Client(transport=_mock_transport(200, payload))
    app = GreenhouseApplicator(
        client=client, profile=profile, bank=bank, track="swe",
        dry_runs_dir=tmp_path / "dry",
    )
    result = app.apply(_job(), dry_run=True)
    assert result.outcome == "review"
    assert any("llm:question_why" in r for r in result.review_reasons)


def test_greenhouse_submit_playwright_ok(profile, bank, tmp_path):
    """submit() calls the Playwright path; mock it returning success."""
    from unittest.mock import patch

    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4\n")
    client = httpx.Client(transport=_mock_transport(200, _GH_FORM_PAYLOAD))
    app = GreenhouseApplicator(
        client=client, profile=profile, bank=bank, track="swe",
        resume_path=str(resume), dry_runs_dir=tmp_path / "dry",
    )
    mock_result = {"ok": True, "url": "https://boards.greenhouse.io/acme/jobs/123/confirmation", "error": None, "field_errors": []}
    with patch("autoapply.execute.playwright_submit.submit_greenhouse", return_value=mock_result):
        result = app.apply(_job(), dry_run=False)
    assert result.outcome == "ok"
    assert result.error_code == ""


def test_greenhouse_submit_playwright_captcha(profile, bank, tmp_path):
    """submit() routes to outcome='captcha' when CaptchaDetected is raised."""
    from unittest.mock import patch
    from autoapply.execute.playwright_submit import CaptchaDetected

    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4\n")
    client = httpx.Client(transport=_mock_transport(200, _GH_FORM_PAYLOAD))
    app = GreenhouseApplicator(
        client=client, profile=profile, bank=bank, track="swe",
        resume_path=str(resume), dry_runs_dir=tmp_path / "dry",
    )
    with patch(
        "autoapply.execute.playwright_submit.submit_greenhouse",
        side_effect=CaptchaDetected("hCaptcha detected"),
    ):
        result = app.apply(_job(), dry_run=False)
    assert result.outcome == "captcha"
    assert result.error_code == "captcha"


def test_greenhouse_submit_playwright_no_confirmation(profile, bank, tmp_path):
    """submit() returns outcome='failed' when Playwright sees no success signal."""
    from unittest.mock import patch

    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4\n")
    client = httpx.Client(transport=_mock_transport(200, _GH_FORM_PAYLOAD))
    app = GreenhouseApplicator(
        client=client, profile=profile, bank=bank, track="swe",
        resume_path=str(resume), dry_runs_dir=tmp_path / "dry",
    )
    mock_result = {"ok": False, "url": "https://boards.greenhouse.io/acme/jobs/123", "error": "no success signal", "field_errors": []}
    with patch("autoapply.execute.playwright_submit.submit_greenhouse", return_value=mock_result):
        result = app.apply(_job(), dry_run=False)
    assert result.outcome == "failed"
    assert result.error_code == "no_confirmation"


# ---------------------------------------------------------------------------
# Lever applicator
# ---------------------------------------------------------------------------


_LEVER_POSTING = {
    "id": "abc123",
    "text": "Software Engineer, Platform",
    "hostedUrl": "https://jobs.lever.co/netflix/abc123",
    "customQuestions": [
        {
            "id": "q-12",
            "text": "Will you now or in the future require visa sponsorship?",
            "type": "yes/no",
            "required": True,
            "options": [{"text": "Yes"}, {"text": "No"}],
        },
        {
            "id": "q-13",
            "text": "What is your GPA?",
            "type": "text",
            "required": False,
        },
    ],
}


def test_lever_fetch_form_parses_base_plus_custom(profile, bank, tmp_path):
    client = httpx.Client(transport=_mock_transport(200, _LEVER_POSTING))
    app = LeverApplicator(
        client=client, profile=profile, bank=bank, track="swe",
        resume_path=str(tmp_path / "resume.pdf"),
        dry_runs_dir=tmp_path / "dry",
    )
    specs = app.fetch_form(_job(source="lever", token="netflix", sid="abc123"))
    by_name = {s.name: s for s in specs}
    # Base fields always present.
    assert "name" in by_name
    assert "email" in by_name
    assert "resume" in by_name and by_name["resume"].kind == "file"
    # Custom questions namespaced under cards[...]
    assert "cards[q-12]" in by_name
    assert by_name["cards[q-12]"].kind == "select"
    assert "Yes" in by_name["cards[q-12]"].options


def test_lever_apply_dry_run_resolves_custom_questions(profile, bank, tmp_path):
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4\n")
    client = httpx.Client(transport=_mock_transport(200, _LEVER_POSTING))
    app = LeverApplicator(
        client=client, profile=profile, bank=bank, track="swe",
        resume_path=str(resume), dry_runs_dir=tmp_path / "dry",
    )
    result = app.apply(
        _job(source="lever", token="netflix", sid="abc123"), dry_run=True
    )
    assert result.outcome == "dry_run", result
    answers = result.answers
    assert answers["cards[q-12]"] == "Yes"   # future sponsorship required (F-1/OPT → H-1B)
    assert answers["cards[q-13]"] == "3.975"  # GPA from profile


def test_lever_submit_playwright_ok(profile, bank, tmp_path):
    """submit() calls the Playwright path; mock it returning success."""
    from unittest.mock import patch

    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4\n")
    client = httpx.Client(transport=_mock_transport(200, _LEVER_POSTING))
    app = LeverApplicator(
        client=client, profile=profile, bank=bank, track="swe",
        resume_path=str(resume), dry_runs_dir=tmp_path / "dry",
    )
    mock_result = {"ok": True, "url": "https://jobs.lever.co/netflix/abc123/confirmation", "error": None, "field_errors": []}
    with patch("autoapply.execute.playwright_submit.submit_lever", return_value=mock_result):
        result = app.apply(
            _job(source="lever", token="netflix", sid="abc123"), dry_run=False
        )
    assert result.outcome == "ok"
    assert result.error_code == ""


def test_lever_submit_playwright_captcha(profile, bank, tmp_path):
    """submit() routes to outcome='captcha' when CaptchaDetected is raised."""
    from unittest.mock import patch
    from autoapply.execute.playwright_submit import CaptchaDetected

    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4\n")
    client = httpx.Client(transport=_mock_transport(200, _LEVER_POSTING))
    app = LeverApplicator(
        client=client, profile=profile, bank=bank, track="swe",
        resume_path=str(resume), dry_runs_dir=tmp_path / "dry",
    )
    with patch(
        "autoapply.execute.playwright_submit.submit_lever",
        side_effect=CaptchaDetected("reCAPTCHA detected"),
    ):
        result = app.apply(
            _job(source="lever", token="netflix", sid="abc123"), dry_run=False
        )
    assert result.outcome == "captcha"
    assert result.error_code == "captcha"


def test_lever_submit_playwright_no_confirmation(profile, bank, tmp_path):
    """submit() returns outcome='failed' when Playwright sees no success signal."""
    from unittest.mock import patch

    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4\n")
    client = httpx.Client(transport=_mock_transport(200, _LEVER_POSTING))
    app = LeverApplicator(
        client=client, profile=profile, bank=bank, track="swe",
        resume_path=str(resume), dry_runs_dir=tmp_path / "dry",
    )
    mock_result = {"ok": False, "url": "https://jobs.lever.co/netflix/abc123/apply", "error": "no success signal", "field_errors": ["upload:resume:TimeoutError"]}
    with patch("autoapply.execute.playwright_submit.submit_lever", return_value=mock_result):
        result = app.apply(
            _job(source="lever", token="netflix", sid="abc123"), dry_run=False
        )
    assert result.outcome == "failed"
    assert result.error_code == "no_confirmation"


# ---------------------------------------------------------------------------
# Applicator base class — dry-run artifact shape + JSON-serializability
# ---------------------------------------------------------------------------


class _StaticApplicator(Applicator):
    name = "static"

    def __init__(self, specs, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._specs = specs

    def fetch_form(self, job):
        return self._specs

    def build_payload(self, job, resolved):
        return {"data": {r.name: r.value for r in resolved}}

    def submit(self, job, payload):
        return ApplyResult(outcome="ok")


def test_applicator_dry_run_dump_is_json_serializable(profile, bank, tmp_path):
    specs = [
        FieldSpec(name="first_name", label="First Name", required=True),
        FieldSpec(name="email", label="Email", required=True),
        FieldSpec(
            name="q_auth",
            label="Are you legally authorized to work in the United States?",
            required=True,
            kind="select",
            options=["Yes", "No"],
        ),
    ]
    app = _StaticApplicator(
        specs, profile=profile, bank=bank, track="swe",
        dry_runs_dir=tmp_path / "dry",
    )
    result = app.apply(_job(), dry_run=True)
    assert result.outcome == "dry_run"
    dumped = json.loads((tmp_path / "dry").glob("*.json").__next__().read_text())
    # Confirm question_type got serialized as a str, not a QuestionType instance.
    qts = [r.get("question_type") for r in dumped["resolved"]]
    assert all(qt is None or isinstance(qt, str) for qt in qts)
