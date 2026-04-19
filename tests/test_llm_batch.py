"""Tests for the batched LLM resolver.

Covers:
  * :func:`resolve_batch` — filter-to-required, no-API-key short-circuit,
    model-cascade on rate-limit / 404, JSON parsing, option validation.
  * :func:`resolve_all_batched` — phase-1 deterministic, phase-2 collect,
    phase-3 batch, phase-4 backfill + audit trail.

All Gemini SDK interactions are stubbed at import time — the suite-wide
``conftest.py`` fixture already blocks live calls, and each test here
replaces the stub with a deterministic scripted response.
"""

from __future__ import annotations

import json

import pytest

from autoapply.answers.llm_batch import (
    BatchAnswer,
    BatchQuestion,
    BatchResult,
    MODEL_CASCADE,
    _is_cascade_error,
    _parse_response,
    _validate_select_value,
    resolve_batch,
)


# ─── unit: _is_cascade_error ──────────────────────────────────────────────


@pytest.mark.parametrize("msg", [
    "RESOURCE_EXHAUSTED",
    "429: quota exceeded",
    "Error: Quota exceeded",
    "rate limit",
    "rate_limit_exceeded",
    "404 NOT_FOUND",
    "permission denied",
    "PERMISSION_DENIED for model",
])
def test_is_cascade_error_triggers(msg):
    assert _is_cascade_error(msg), (
        f"expected cascade-triggering for {msg!r}"
    )


@pytest.mark.parametrize("msg", [
    "Authentication failed",
    "malformed request",
    "invalid prompt",
    "server returned 500 Internal",
])
def test_is_cascade_error_non_cascade(msg):
    assert not _is_cascade_error(msg), (
        f"unexpected cascade trigger for {msg!r}"
    )


# ─── unit: _validate_select_value ─────────────────────────────────────────


def test_validate_select_value_exact():
    assert _validate_select_value("Yes", ["Yes", "No"]) == "Yes"


def test_validate_select_value_case_insensitive_returns_canonical():
    # Should return the original-case option, not the LLM's casing.
    assert _validate_select_value("yes", ["Yes", "No"]) == "Yes"


def test_validate_select_value_rejects_substring():
    # "yes" is a substring of "Yes, definitely" but NOT an exact match.
    # Substring fallback is unsafe — can match unrelated options. Reject.
    assert _validate_select_value(
        "yes definitely", ["Yes", "No", "Maybe"]
    ) is None


def test_validate_select_value_rejects_non_string():
    assert _validate_select_value(None, ["Yes", "No"]) is None
    assert _validate_select_value(42, ["Yes", "No"]) is None


# ─── unit: _parse_response ────────────────────────────────────────────────


def _qs(*specs):
    """Helper — build BatchQuestion list from (id, kind, options) triples."""
    out = []
    for spec in specs:
        if len(spec) == 2:
            qid, kind = spec
            opts = []
        else:
            qid, kind, opts = spec
        out.append(BatchQuestion(
            id=qid, label=qid, kind=kind, required=True, options=list(opts),
        ))
    return out


def test_parse_response_happy_path():
    questions = _qs(
        ("q1", "select", ("Yes", "No")),
        ("q2", "text"),
    )
    raw = json.dumps({
        "answers": [
            {"id": "q1", "value": "Yes", "source": "llm_reasoning",
             "confidence": 0.95, "reasoning": "profile matches"},
            {"id": "q2", "value": "College Park", "source": "llm_reasoning",
             "confidence": 0.9, "reasoning": "profile location"},
        ]
    })
    out = _parse_response(raw, questions)
    assert set(out) == {"q1", "q2"}
    assert out["q1"].value == "Yes"
    assert out["q2"].value == "College Park"
    assert out["q1"].source == "llm_reasoning"


def test_parse_response_strips_markdown_fences():
    questions = _qs(("q1", "text"))
    raw = """```json
{"answers":[{"id":"q1","value":"foo","source":"llm_reasoning","confidence":0.9}]}
```"""
    out = _parse_response(raw, questions)
    assert out["q1"].value == "foo"


def test_parse_response_select_option_mismatch_goes_to_review():
    """LLM returns a value not in the option list → marked needs_review."""
    questions = _qs(("q1", "select", ("He/Him", "She/Her", "They/Them")))
    raw = json.dumps({
        "answers": [{"id": "q1", "value": "He/Him/His",  # not a valid option
                     "source": "llm_reasoning", "confidence": 0.95}]
    })
    out = _parse_response(raw, questions)
    assert out["q1"].source == "needs_review"
    assert out["q1"].value is None


def test_parse_response_low_confidence_forced_to_review():
    """confidence < 0.5 → forced to needs_review regardless of source."""
    questions = _qs(("q1", "select", ("Yes", "No")))
    raw = json.dumps({
        "answers": [{"id": "q1", "value": "Yes", "source": "llm_reasoning",
                     "confidence": 0.3}]
    })
    out = _parse_response(raw, questions)
    assert out["q1"].source == "needs_review"
    assert out["q1"].value is None


def test_parse_response_missing_question_backfilled_as_review():
    """LLM omitted a question → placeholder with needs_review."""
    questions = _qs(("q1", "text"), ("q2", "text"))
    raw = json.dumps({
        "answers": [{"id": "q1", "value": "answered", "source": "llm_reasoning",
                     "confidence": 0.9}]
    })
    out = _parse_response(raw, questions)
    assert out["q1"].value == "answered"
    assert out["q2"].source == "needs_review"
    assert out["q2"].value is None


def test_parse_response_handles_malformed_json():
    """Invalid JSON → empty dict (caller treats as failure)."""
    questions = _qs(("q1", "text"))
    out = _parse_response("not json at all", questions)
    assert out == {}


def test_parse_response_multi_select():
    """Multi-select answers come back as a list of option strings."""
    questions = _qs(
        ("q1", "multi_select",
         ("California", "New York", "Texas", "Washington")),
    )
    raw = json.dumps({
        "answers": [{"id": "q1", "value": ["California", "New York"],
                     "source": "llm_reasoning", "confidence": 0.9}]
    })
    out = _parse_response(raw, questions)
    # Multi-select keeps the list; caller joins to ", " downstream.
    assert out["q1"].value == ["California", "New York"]


# ─── integration: resolve_batch with scripted cascade ─────────────────────


def _stub_cascade_factory(model_response_sequence):
    """Build a stub for ``_call_with_cascade`` that steps through a scripted
    sequence of ``(model_id, outcome)`` tuples. ``outcome`` is either a
    dict of ``{question_id: BatchAnswer}`` (success) or an Exception to
    raise (cascade trigger) or a ``BatchResult`` (explicit failure).
    """
    def _stub(prompt, api_key, questions):
        # For tests we ignore the cascade logic and just return the first
        # programmed answer set. Real cascade logic is tested separately.
        _model, outcome = model_response_sequence[0]
        if isinstance(outcome, BatchResult):
            return outcome
        return BatchResult(
            answers=outcome,
            model_used=_model,
            cascade_trace=[{"model": _model, "ok": True}],
        )
    return _stub


def test_resolve_batch_no_api_key_short_circuits(monkeypatch):
    """No API key → returns error without attempting any cascade call."""
    monkeypatch.setenv("GEMINI_API_KEY", "")
    # Force settings singleton to re-read env vars.
    import autoapply.config as _cfg
    _cfg._settings = None

    # Minimal fake profile for the signature.
    from autoapply.profile.schema import Profile, Skills
    fake = Profile(
        track="swe", full_name="Test", email="t@e.com", phone="",
        linkedin_url="", github_url="", education=[], experiences=[],
        projects=[], skills=Skills(), years_of_experience={},
    )

    result = resolve_batch(
        questions=_qs(("q1", "select", ("Yes", "No"))),
        profile=fake,
        answer_bank_yaml="",
    )
    assert result.error == "GEMINI_API_KEY not set"
    assert result.answers == {}


def test_resolve_batch_filters_to_required(monkeypatch):
    """Non-required questions are filtered out BEFORE the LLM call."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    # Force settings singleton to re-read env vars.
    import autoapply.config as _cfg
    _cfg._settings = None

    from autoapply.profile.schema import Profile, Skills
    fake = Profile(
        track="swe", full_name="T", email="", phone="", linkedin_url="",
        github_url="", education=[], experiences=[], projects=[],
        skills=Skills(), years_of_experience={},
    )

    qs = [
        BatchQuestion(id="req", label="r", kind="select",
                      required=True, options=["A", "B"]),
        BatchQuestion(id="opt", label="o", kind="select",
                      required=False, options=["X", "Y"]),
    ]

    captured_questions: list[list[BatchQuestion]] = []
    def _stub(prompt, api_key, questions):
        captured_questions.append(questions)
        return BatchResult(
            answers={"req": BatchAnswer(
                question_id="req", value="A", source="llm_reasoning",
                confidence=0.9,
            )},
            model_used="test-model",
        )
    monkeypatch.setattr(
        "autoapply.answers.llm_batch._call_with_cascade", _stub,
    )

    result = resolve_batch(
        questions=qs, profile=fake, answer_bank_yaml="",
    )
    # Only the required question should have been sent.
    assert len(captured_questions) == 1
    sent_ids = [q.id for q in captured_questions[0]]
    assert sent_ids == ["req"]
    assert "opt" not in result.answers


def test_resolve_batch_no_required_questions_no_call(monkeypatch):
    """Empty required set → no cascade call at all."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    # Force settings singleton to re-read env vars.
    import autoapply.config as _cfg
    _cfg._settings = None

    from autoapply.profile.schema import Profile, Skills
    fake = Profile(
        track="swe", full_name="T", email="", phone="", linkedin_url="",
        github_url="", education=[], experiences=[], projects=[],
        skills=Skills(), years_of_experience={},
    )

    called = []
    def _stub(prompt, api_key, questions):
        called.append(True)
        return BatchResult()
    monkeypatch.setattr(
        "autoapply.answers.llm_batch._call_with_cascade", _stub,
    )

    # All optional → zero required after filter.
    qs = [BatchQuestion(id="x", label="x", kind="select",
                         required=False, options=["A"])]
    result = resolve_batch(
        questions=qs, profile=fake, answer_bank_yaml="",
    )
    assert called == []  # no cascade call
    assert result.answers == {}
    assert result.error == ""


# ─── MODEL_CASCADE config sanity ──────────────────────────────────────────


def test_model_cascade_order_matches_user_spec():
    """User specified: 3.1 Lite → 2.5 Lite → 3 Flash → 2.5 Flash.
    Regression test — changing this order is a deliberate policy change
    that should be reviewed."""
    assert MODEL_CASCADE == (
        "gemini-3.1-flash-lite",
        "gemini-2.5-flash-lite",
        "gemini-3-flash",
        "gemini-2.5-flash",
    )


def test_model_cascade_does_not_include_deprecated_models():
    """gemini-2.0-flash and 2.0-flash-lite were deprecated Mar 2026;
    must NOT appear in the cascade."""
    for deprecated in ("gemini-2.0-flash", "gemini-2.0-flash-lite"):
        assert deprecated not in MODEL_CASCADE, (
            f"deprecated model {deprecated!r} in cascade"
        )


# ─── integration: resolve_all_batched end-to-end ──────────────────────────


@pytest.fixture
def fake_profile():
    from autoapply.profile.schema import (
        DateRange, Education, Profile, Skills,
    )
    return Profile(
        track="swe",
        full_name="Aadit Nilay",
        email="aaditnilay18@gmail.com",
        phone="510-610-2180",
        linkedin_url="https://linkedin.com/in/aadit-nilay",
        github_url="https://github.com/aaditn18",
        education=[Education(
            school="University of Maryland",
            degree="B.S. Computer Science",
            minor="", gpa="3.975", coursework=[],
            date_range=DateRange(raw="May 2026", start=None, end=None,
                                  is_present=False),
        )],
        experiences=[], projects=[],
        skills=Skills(),
        years_of_experience={"Python": 3.0, "C++": 2.0},
    )


@pytest.fixture
def fake_bank(tmp_path):
    from autoapply.answers.bank import AnswerBank
    return AnswerBank.from_dict({})


def test_resolve_all_batched_phase1_answers_all(monkeypatch, fake_profile, fake_bank):
    """When Phase 1 answers every required field, the batch call is skipped."""
    from autoapply.execute.standard_fields import FieldSpec, resolve_all_batched

    called = []
    def _stub(prompt, api_key, questions):
        called.append(True)
        return BatchResult()
    monkeypatch.setattr(
        "autoapply.answers.llm_batch._call_with_cascade", _stub,
    )

    specs = [
        FieldSpec(name="first_name", label="First Name", required=True, kind="text"),
        FieldSpec(name="email", label="Email", required=True, kind="text"),
    ]
    resolved, unresolved, audit = resolve_all_batched(
        specs, profile=fake_profile, bank=fake_bank, track="swe",
    )
    assert len(resolved) == 2
    assert not unresolved
    assert called == []  # no batch call
    assert audit["batch_asked"] == []


def test_resolve_all_batched_collects_unresolved_select(monkeypatch, fake_profile, fake_bank):
    """Required select without a Phase-1 match → sent to batch → answer backfilled."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    # Force settings singleton to re-read env vars.
    import autoapply.config as _cfg
    _cfg._settings = None

    from autoapply.execute.standard_fields import FieldSpec, resolve_all_batched

    captured: list[list[BatchQuestion]] = []
    def _stub(prompt, api_key, questions):
        captured.append(questions)
        return BatchResult(
            answers={"question_gender": BatchAnswer(
                question_id="question_gender",
                value="Prefer not to say",
                source="llm_reasoning",
                confidence=0.95,
            )},
            model_used="gemini-2.5-flash-lite",
            cascade_trace=[{"model": "gemini-2.5-flash-lite", "ok": True}],
        )
    monkeypatch.setattr(
        "autoapply.answers.llm_batch._call_with_cascade", _stub,
    )

    specs = [
        FieldSpec(
            name="question_gender", label="Gender",
            required=True, kind="select",
            options=["Male", "Female", "Prefer not to say"],
        ),
    ]
    resolved, unresolved, audit = resolve_all_batched(
        specs, profile=fake_profile, bank=fake_bank, track="swe",
        company="Acme", job_title="Software Engineer",
    )
    # Batch was called with the gender question.
    assert len(captured) == 1
    assert captured[0][0].id == "question_gender"
    # The resolved value is the LLM's pick, tagged with llm_batch source.
    gender = [r for r in resolved if r.name == "question_gender"][0]
    assert gender.value == "Prefer not to say"
    assert gender.source.startswith("llm_batch")
    assert audit["model_used"] == "gemini-2.5-flash-lite"
    assert audit["answer_count"] == 1


def test_resolve_all_batched_skips_optional(monkeypatch, fake_profile, fake_bank):
    """Non-required questions with no deterministic match are left blank,
    never sent to the batch."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    # Force settings singleton to re-read env vars.
    import autoapply.config as _cfg
    _cfg._settings = None

    from autoapply.execute.standard_fields import FieldSpec, resolve_all_batched

    captured = []
    def _stub(prompt, api_key, questions):
        captured.append([q.id for q in questions])
        return BatchResult(answers={}, model_used="none")
    monkeypatch.setattr(
        "autoapply.answers.llm_batch._call_with_cascade", _stub,
    )

    specs = [
        FieldSpec(
            name="question_opt", label="Optional question",
            required=False, kind="select", options=["A", "B"],
        ),
    ]
    resolved, unresolved, audit = resolve_all_batched(
        specs, profile=fake_profile, bank=fake_bank, track="swe",
    )
    # Nothing required → nothing sent to batch → no call.
    assert captured == []
    assert audit["batch_asked"] == []


def test_education_option_preferences():
    """Preference lists must pick canonical phrasings in priority order.

    The order matters: `Bachelor of Science` should beat `B.S.` when both
    appear, and `Computer Science` should beat `Computer and Information
    Sciences`. This is what lets the pre-resolve pass deterministically
    pick the "correct" option on tenants that list multiple synonyms.
    """
    from autoapply.execute.submitter.dom_batch import (
        _DEGREE_OPTION_PREFERENCES,
        _DISCIPLINE_OPTION_PREFERENCES,
        _SCHOOL_OPTION_PREFERENCES,
        _match_preferred_option,
    )

    # Degree: Bachelor of Science beats B.S. beats Bachelor's Degree.
    assert _match_preferred_option(
        ["Bachelor of Science", "Master of Science", "B.S.", "Bachelor's Degree"],
        _DEGREE_OPTION_PREFERENCES,
    ) == "Bachelor of Science"
    assert _match_preferred_option(
        ["Bachelor's Degree", "Master of Science"],
        _DEGREE_OPTION_PREFERENCES,
    ) == "Bachelor's Degree"
    assert _match_preferred_option(
        ["B.S.", "M.S.", "Ph.D."],
        _DEGREE_OPTION_PREFERENCES,
    ) == "B.S."

    # Discipline: Computer Science beats Computer and Information Sciences.
    assert _match_preferred_option(
        ["Computer Science", "Mathematics", "Electrical Engineering"],
        _DISCIPLINE_OPTION_PREFERENCES,
    ) == "Computer Science"
    assert _match_preferred_option(
        ["Computer and Information Sciences", "Computer Sciences"],
        _DISCIPLINE_OPTION_PREFERENCES,
    ) == "Computer and Information Sciences"

    # School: all punctuation variants of UMD College Park should match.
    for variant in (
        "University of Maryland, College Park",
        "University of Maryland - College Park",
        "University of Maryland College Park",
        "University of Maryland – College Park",   # en-dash
        "University of Maryland/College Park",
    ):
        assert _match_preferred_option(
            [variant, "Other University"],
            _SCHOOL_OPTION_PREFERENCES,
        ) == variant, f"variant not matched: {variant!r}"

    # Fall through to plain "University of Maryland" when no variant
    # with College Park is listed.
    assert _match_preferred_option(
        ["University of Maryland"],
        _SCHOOL_OPTION_PREFERENCES,
    ) == "University of Maryland"


def test_resolve_all_batched_llm_decline_preserves_review(
    monkeypatch, fake_profile, fake_bank,
):
    """LLM returns needs_review → required field stays unresolved → review."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    # Force settings singleton to re-read env vars.
    import autoapply.config as _cfg
    _cfg._settings = None

    from autoapply.execute.standard_fields import FieldSpec, resolve_all_batched

    def _stub(prompt, api_key, questions):
        return BatchResult(
            answers={"q_mystery": BatchAnswer(
                question_id="q_mystery",
                value=None,
                source="needs_review",
                confidence=0.0,
                reasoning="insufficient context",
            )},
            model_used="gemini-2.5-flash-lite",
        )
    monkeypatch.setattr(
        "autoapply.answers.llm_batch._call_with_cascade", _stub,
    )

    specs = [
        FieldSpec(
            name="q_mystery",
            label="Describe a time you navigated a vegetable emergency.",
            required=True, kind="textarea",
        ),
    ]
    resolved, unresolved, audit = resolve_all_batched(
        specs, profile=fake_profile, bank=fake_bank, track="swe",
    )
    # Field remains unresolved (batch declined).
    mystery = [r for r in resolved if r.name == "q_mystery"]
    # Either it's unresolved or it's resolved with requires_review=True.
    # Behavior: Phase 1 raised UnresolvedField → stays in unresolved.
    assert any(u.name == "q_mystery" for u in unresolved) or (
        mystery and mystery[0].requires_review
    )
