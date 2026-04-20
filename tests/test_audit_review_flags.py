"""Tests for the Phase-7 extractions from base.py.

Two previously-inlined concerns moved to their own modules:

- ``execute/audit.py`` owns the per-field audit logging (was a 75-LOC
  method on Applicator). Now a pure function we can test directly.
- ``execute/review_flags.py`` owns the review-queue payload build (was
  inlined in Applicator.apply). Now a pure function returning
  ``(reasons, flags)``.

This file locks down:

1. **bucket_source** — source-string → bucket-name mapping (regression
   test for the "profile"/"bank"/"classifier"/"llm_batch" taxonomy).
2. **log_resolution_audit** — emits the expected log lines; "none" +
   empty value is hidden; stable bucket order.
3. **build_review_payload** — both unresolved and require_llm/review
   fields land in the flag list; reasons are compact tags.
"""

from __future__ import annotations

import logging

from autoapply.execute.standard_fields import (
    FieldSpec,
    ResolvedField,
    UnresolvedField,
)


# ─── bucket_source ──────────────────────────────────────────────────────


def test_bucket_source_maps_core_sources():
    from autoapply.execute.audit import bucket_source

    assert bucket_source("machine_key") == "profile"
    assert bucket_source("profile") == "profile"
    assert bucket_source("bank") == "bank"
    assert bucket_source("bank:quant") == "bank"
    assert bucket_source("classifier+bank") == "classifier"
    assert bucket_source("llm_batch") == "llm_batch"
    assert bucket_source("llm_batch:llm_generation") == "llm_batch"
    assert bucket_source("llm_answer") == "llm_single"
    assert bucket_source("review_required") == "review"
    assert bucket_source("llm_required") == "review"
    assert bucket_source("") == "none"
    assert bucket_source("none") == "none"
    # Unknown source passes through.
    assert bucket_source("custom_foo") == "custom_foo"


# ─── log_resolution_audit ───────────────────────────────────────────────


def test_log_resolution_audit_hides_empty_none_entries(caplog):
    from autoapply.execute.audit import log_resolution_audit

    resolved = [
        ResolvedField(name="first_name", label="First", value="Aadit", source="machine_key"),
        # Optional field that Phase 1 couldn't answer — intentionally empty.
        ResolvedField(name="pronouns", label="Pronouns", value="", source="none"),
    ]
    with caplog.at_level(logging.INFO, logger="autoapply.execute.audit"):
        log_resolution_audit(resolved, [])

    msgs = [r.getMessage() for r in caplog.records]
    combined = "\n".join(msgs)
    # Empty "none" entry is hidden — only first_name shows up.
    assert "first_name" in combined
    assert "pronouns" not in combined
    # Header says 1 answered (because the empty-none entry is filtered).
    assert any("1 answered" in m for m in msgs)


def test_log_resolution_audit_shows_unresolved_as_review_lines(caplog):
    from autoapply.execute.audit import log_resolution_audit

    resolved = [
        ResolvedField(name="first_name", label="First", value="Aadit", source="profile"),
    ]
    unresolved = [
        UnresolvedField(label="Why this company?", name="why_company", reason="review"),
    ]
    with caplog.at_level(logging.INFO, logger="autoapply.execute.audit"):
        log_resolution_audit(resolved, unresolved)

    msgs = [r.getMessage() for r in caplog.records]
    combined = "\n".join(msgs)
    assert "1 answered, 1 unresolved" in combined
    assert "why_company" in combined
    assert "unresolved: Why this company?" in combined


def test_log_resolution_audit_bucket_order_stable(caplog):
    """Bucket order: profile → bank → classifier → llm_batch → ..."""
    from autoapply.execute.audit import log_resolution_audit

    resolved = [
        ResolvedField(name="q1", label="L1", value="v1", source="llm_batch:llm_generation"),
        ResolvedField(name="q2", label="L2", value="v2", source="profile"),
        ResolvedField(name="q3", label="L3", value="v3", source="classifier+bank"),
    ]
    with caplog.at_level(logging.INFO, logger="autoapply.execute.audit"):
        log_resolution_audit(resolved, [])

    # Filter to the per-field lines (starts with leading spaces + "[").
    field_lines = [
        r.getMessage() for r in caplog.records
        if r.getMessage().lstrip().startswith("[")
    ]
    # q2 (profile) before q3 (classifier) before q1 (llm_batch).
    profile_idx = next(i for i, m in enumerate(field_lines) if "q2" in m)
    classifier_idx = next(i for i, m in enumerate(field_lines) if "q3" in m)
    llm_idx = next(i for i, m in enumerate(field_lines) if "q1" in m)
    assert profile_idx < classifier_idx < llm_idx


# ─── build_review_payload ───────────────────────────────────────────────


def test_build_review_payload_empty_when_everything_resolved():
    from autoapply.execute.review_flags import build_review_payload

    specs = [FieldSpec(name="first_name", label="First", required=True)]
    resolved = [
        ResolvedField(name="first_name", label="First", value="Aadit", source="profile"),
    ]
    reasons, flags = build_review_payload(specs, resolved, [])
    assert reasons == []
    assert flags == []


def test_build_review_payload_unresolved_fields():
    from autoapply.execute.review_flags import build_review_payload

    specs = [
        FieldSpec(name="gender", label="Gender", required=True, kind="select",
                  options=["Male", "Female"]),
    ]
    unresolved = [
        UnresolvedField(label="Gender", name="gender", reason="no match"),
    ]
    reasons, flags = build_review_payload(specs, [], unresolved)
    assert reasons == ["unresolved:gender"]
    assert len(flags) == 1
    assert flags[0]["field_name"] == "gender"
    assert flags[0]["reason"] == "unresolved"
    assert flags[0]["options"] == ["Male", "Female"]


def test_build_review_payload_requires_llm_and_review():
    from autoapply.execute.review_flags import build_review_payload

    specs = [
        FieldSpec(name="why_company", label="Why this company?",
                  required=True, kind="textarea"),
        FieldSpec(name="felony", label="Convicted of a felony?",
                  required=True, kind="select", options=["Yes", "No"]),
    ]
    resolved = [
        ResolvedField(
            name="why_company", label="Why this company?", value="",
            source="llm_required", requires_llm=True,
        ),
        ResolvedField(
            name="felony", label="Convicted of a felony?", value="",
            source="review_required", requires_review=True,
        ),
    ]
    reasons, flags = build_review_payload(specs, resolved, [])
    # Reasons: one per flagged field, compact tags.
    assert "llm:why_company" in reasons
    assert "review:felony" in reasons
    # Flags: correct reason tags per field.
    flag_by_name = {f["field_name"]: f for f in flags}
    assert flag_by_name["why_company"]["reason"] == "requires_llm"
    assert flag_by_name["felony"]["reason"] == "requires_review"


def test_build_review_payload_ignores_resolved_fields_without_flags():
    """Resolved fields without requires_llm / requires_review should NOT
    appear in the review payload — they're real answers."""
    from autoapply.execute.review_flags import build_review_payload

    specs = [
        FieldSpec(name="first_name", label="First", required=True),
        FieldSpec(name="why_company", label="Why?", required=True, kind="textarea"),
    ]
    resolved = [
        ResolvedField(name="first_name", label="First", value="Aadit", source="profile"),
        ResolvedField(
            name="why_company", label="Why?", value="",
            source="llm_required", requires_llm=True,
        ),
    ]
    reasons, flags = build_review_payload(specs, resolved, [])
    assert reasons == ["llm:why_company"]
    assert [f["field_name"] for f in flags] == ["why_company"]


# ─── bank._from_profile: shrunken function still works ──────────────────


def _make_minimal_profile():
    from autoapply.profile.schema import Profile
    return Profile(
        track="swe",
        full_name="Aadit Nilay",
        email="a@example.com",
        phone="5551212",
        linkedin_url="https://linkedin.com/in/aadit",
        github_url="https://github.com/aadit",
        education=[],
        experiences=[],
        projects=[],
        years_of_experience={},
    )


def test_from_profile_simple_attr_lookup_still_works():
    from autoapply.answers.bank import _from_profile
    from autoapply.answers.types import QuestionType

    p = _make_minimal_profile()
    assert _from_profile(QuestionType.EMAIL, p, {}) == "a@example.com"
    assert _from_profile(QuestionType.PHONE, p, {}) == "5551212"
    assert _from_profile(QuestionType.LINKEDIN_URL, p, {}) == "https://linkedin.com/in/aadit"


def test_from_profile_name_splitting():
    from autoapply.answers.bank import _from_profile
    from autoapply.answers.types import QuestionType

    p = _make_minimal_profile()
    assert _from_profile(QuestionType.FULL_NAME, p, {}) == "Aadit Nilay"
    assert _from_profile(QuestionType.FIRST_NAME, p, {}) == "Aadit"
    assert _from_profile(QuestionType.LAST_NAME, p, {}) == "Nilay"
    # Preferred-name falls back to first.
    assert _from_profile(QuestionType.PREFERRED_NAME, p, {}) == "Aadit"


def test_from_profile_yoe_returns_zero_for_unknown_skill():
    """Regression: unknown skill → "0", not None (most ATS forms reject
    an empty numeric answer)."""
    from autoapply.answers.bank import _from_profile
    from autoapply.answers.types import QuestionType

    p = _make_minimal_profile()
    assert _from_profile(
        QuestionType.YOE_LANGUAGE, p, {"skill": "Haskell"},
    ) == "0"


def test_from_profile_known_null_type_returns_none():
    """PORTFOLIO_URL has no Profile field yet — should return None
    so the caller routes to review rather than pretending we don't
    know about the type."""
    from autoapply.answers.bank import _from_profile
    from autoapply.answers.types import QuestionType

    p = _make_minimal_profile()
    assert _from_profile(QuestionType.PORTFOLIO_URL, p, {}) is None
    assert _from_profile(QuestionType.WEBSITE_URL, p, {}) is None


def test_from_profile_dict_growth_path_documented():
    """Regression lock: every entry in _SIMPLE_PROFILE_ATTRS must point
    at an existing Profile field — otherwise a rename silently breaks
    an answer path."""
    from autoapply.answers.bank import _SIMPLE_PROFILE_ATTRS
    from autoapply.profile.schema import Profile

    # Build a minimal Profile (uses schema defaults for all the
    # common-across-tracks fields — the ones the dict targets).
    p = _make_minimal_profile()
    for qt, attr in _SIMPLE_PROFILE_ATTRS.items():
        assert hasattr(p, attr), (
            f"_SIMPLE_PROFILE_ATTRS[{qt!r}] = {attr!r} "
            f"but Profile has no such field"
        )
