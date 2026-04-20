"""Tests for the execute/resolution/ package.

The resolution package was split from standard_fields.py (575 LOC).
This file locks down:

1. **Backwards compatibility** — the old ``standard_fields`` names
   still resolve, and resolve to the SAME objects the new package
   exports.
2. **Pure-function behavior** — batch_builder's `_value_matches_option`
   and backfill's `_serialize_answer_value` are trivially unit-testable.
3. **Composition contract** — build_batch's output respects the
   "leave-optional-blank" and "don't-batch-machine-key" invariants.
"""

from __future__ import annotations

from autoapply.execute.standard_fields import (
    FieldSpec,
    ResolvedField,
    UnresolvedField,
)


# ─── Backwards-compat ────────────────────────────────────────────────────


def test_standard_fields_exports_same_objects_as_resolution():
    from autoapply.execute import standard_fields
    from autoapply.execute.resolution import (
        backfill,
        batch_builder,
        machine_key,
        options_snap,
        orchestrator,
        phase1,
    )

    assert standard_fields.resolve_field is phase1.resolve_field
    assert standard_fields.resolve_all is phase1.resolve_all
    assert standard_fields.resolve_all_batched is orchestrator.resolve_all_batched
    assert standard_fields._match_machine_key is machine_key._match_machine_key
    assert standard_fields._profile_value is machine_key._profile_value
    assert standard_fields._snap_to_option is options_snap._snap_to_option
    assert standard_fields._value_matches_option is batch_builder._value_matches_option


# ─── Pure-function tests ────────────────────────────────────────────────


def test_value_matches_option_exact_case_insensitive():
    from autoapply.execute.resolution.batch_builder import _value_matches_option

    assert _value_matches_option("Yes", ["yes", "no"]) is True
    assert _value_matches_option("YES", ["Yes", "No"]) is True
    # Substring does NOT count (React-Select rejects substrings post-submit).
    assert _value_matches_option("University of Maryland",
                                 ["University of Maryland - College Park"]) is False
    # Empty inputs.
    assert _value_matches_option("", ["yes"]) is False
    assert _value_matches_option("yes", []) is False


def test_serialize_answer_value_list_joined_with_comma():
    from autoapply.execute.resolution.backfill import _serialize_answer_value

    assert _serialize_answer_value(["a", "b", "c"]) == "a, b, c"
    assert _serialize_answer_value([]) == ""
    assert _serialize_answer_value(None) == ""
    assert _serialize_answer_value("plain string") == "plain string"
    # Non-string scalars are stringified.
    assert _serialize_answer_value(42) == "42"


# ─── build_batch composition tests ──────────────────────────────────────


def test_build_batch_skips_machine_key_resolved_fields():
    """Machine-key fields (first_name, email, resume_text) must NEVER
    be batched even when their value is empty — batching resume_text
    would make the LLM paste the full resume into a field the form
    ignores."""
    from autoapply.execute.resolution.batch_builder import build_batch

    specs = [
        FieldSpec(name="resume_text", label="Resume text", required=True, kind="textarea"),
    ]
    # Phase-1 machine_key-resolved, empty value (the deliberate case).
    resolved = [ResolvedField(
        name="resume_text", label="Resume text", value="", source="machine_key",
    )]
    plan = build_batch(specs, resolved, [])
    assert plan.questions == []


def test_build_batch_batches_unconfident_required_select():
    from autoapply.execute.resolution.batch_builder import build_batch

    specs = [
        FieldSpec(
            name="degree", label="Degree", required=True, kind="select",
            options=["Bachelor of Science", "Master of Science"],
        ),
    ]
    # Phase-1 resolved but the value doesn't exactly match an option.
    resolved = [ResolvedField(
        name="degree", label="Degree", value="B.S.", source="classifier+bank",
    )]
    plan = build_batch(specs, resolved, [])
    assert len(plan.questions) == 1
    assert plan.questions[0].id == "degree"
    assert plan.questions[0].options == ["Bachelor of Science", "Master of Science"]
    # Backfill-map tracks the original ResolvedField.
    assert "degree" in plan.backfill_map
    assert plan.backfill_map["degree"] is resolved[0]


def test_build_batch_leaves_optional_fields_blank():
    """Optional fields that Phase 1 couldn't answer are LEFT BLANK by
    design — per the 'leave blank' policy, we don't burn LLM tokens on
    optional questions."""
    from autoapply.execute.resolution.batch_builder import build_batch

    specs = [
        FieldSpec(name="pronouns", label="Pronouns", required=False, kind="select",
                  options=["He/Him", "She/Her"]),
    ]
    resolved = [ResolvedField(
        name="pronouns", label="Pronouns", value="",
        source="none",  # Phase-1 couldn't answer (unknown type).
    )]
    plan = build_batch(specs, resolved, [])
    assert plan.questions == []


def test_build_batch_promotes_unresolved_required_fields():
    from autoapply.execute.resolution.batch_builder import build_batch

    specs = [
        FieldSpec(name="why_company", label="Why this company?",
                  required=True, kind="textarea"),
    ]
    unresolved = [UnresolvedField(
        label="Why this company?", name="why_company",
        reason="LLM_REQUIRED",
    )]
    plan = build_batch(specs, [], unresolved)
    assert len(plan.questions) == 1
    assert plan.questions[0].id == "why_company"
    # Unresolved → synthesized downstream; no backfill_map entry.
    assert "why_company" not in plan.backfill_map


def test_build_batch_batches_required_text_with_empty_value():
    """Required text fields with an empty Phase-1 value should go to the
    LLM — they'd be rejected by the form otherwise."""
    from autoapply.execute.resolution.batch_builder import build_batch

    specs = [
        FieldSpec(name="salary_expectation", label="Salary expectation",
                  required=True, kind="text"),
    ]
    resolved = [ResolvedField(
        name="salary_expectation", label="Salary expectation",
        value="", source="classifier+bank",
    )]
    plan = build_batch(specs, resolved, [])
    assert len(plan.questions) == 1
    assert plan.questions[0].id == "salary_expectation"


# ─── backfill.apply_answers composition tests ───────────────────────────


def test_apply_answers_promotes_unresolved_to_resolved():
    """When the LLM answers a previously-unresolved question, we add
    a new ResolvedField AND remove the entry from ``unresolved``."""
    from autoapply.answers.llm_batch import BatchAnswer
    from autoapply.execute.resolution.backfill import apply_answers

    specs = [
        FieldSpec(name="why_company", label="Why this company?",
                  required=True, kind="textarea"),
    ]
    resolved: list[ResolvedField] = []
    unresolved = [UnresolvedField(
        label="Why this company?", name="why_company",
        reason="LLM_REQUIRED",
    )]
    answers = {
        "why_company": BatchAnswer(
            question_id="why_company",
            value="I'm drawn to their mission because...",
            source="llm_generation", confidence=0.9, reasoning="matched JD",
        ),
    }
    new_resolved, new_unresolved = apply_answers(
        specs=specs, resolved=resolved, unresolved=unresolved, answers=answers,
    )
    assert len(new_resolved) == 1
    assert new_resolved[0].name == "why_company"
    assert new_resolved[0].source == "llm_batch:llm_generation"
    assert new_unresolved == []


def test_apply_answers_needs_review_keeps_unresolved():
    """When the LLM returns source='needs_review', Phase-1 state stays
    intact — unresolved stays unresolved."""
    from autoapply.answers.llm_batch import BatchAnswer
    from autoapply.execute.resolution.backfill import apply_answers

    specs = [
        FieldSpec(name="felony", label="Have you been convicted?",
                  required=True, kind="select", options=["Yes", "No"]),
    ]
    unresolved = [UnresolvedField(
        label="Have you been convicted?", name="felony", reason="policy-sensitive",
    )]
    answers = {
        "felony": BatchAnswer(
            question_id="felony", value=None,
            source="needs_review", confidence=0.3,
            reasoning="ambiguous / policy-sensitive",
        ),
    }
    new_resolved, new_unresolved = apply_answers(
        specs=specs, resolved=[], unresolved=unresolved, answers=answers,
    )
    assert new_resolved == []
    assert len(new_unresolved) == 1


def test_apply_answers_clears_llm_flags_on_backfill():
    """An existing ResolvedField flagged requires_llm gets its value
    replaced AND both flags cleared — so downstream code doesn't
    re-route the app to review."""
    from autoapply.answers.llm_batch import BatchAnswer
    from autoapply.execute.resolution.backfill import apply_answers

    specs = [
        FieldSpec(name="why_role", label="Why this role?",
                  required=True, kind="textarea"),
    ]
    resolved = [ResolvedField(
        name="why_role", label="Why this role?", value="",
        source="llm_required", requires_llm=True,
    )]
    answers = {
        "why_role": BatchAnswer(
            question_id="why_role",
            value="Because distributed systems are my favorite...",
            source="llm_generation", confidence=0.9, reasoning="matched JD",
        ),
    }
    new_resolved, _ = apply_answers(
        specs=specs, resolved=resolved, unresolved=[], answers=answers,
    )
    r = new_resolved[0]
    assert r.value.startswith("Because distributed systems")
    assert r.source == "llm_batch:llm_generation"
    assert r.requires_llm is False
    assert r.requires_review is False
