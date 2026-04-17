"""Answer bank + classifier tests.

Two concerns in one file because they're tightly coupled:
  1. `classify()` — question string → QuestionType (rule table; must handle
     paraphrases across ~15 canonical types without misfires).
  2. `AnswerBank.answer()` — classified question → resolved Answer, honoring
     the PROFILE_SOURCED / LLM_REQUIRED / REVIEW_REQUIRED policy sets.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from autoapply.answers.bank import Answer, AnswerBank, _extract_major, _format_yoe
from autoapply.answers.classifier import classify
from autoapply.answers.types import (
    LLM_REQUIRED,
    PROFILE_SOURCED,
    REVIEW_REQUIRED,
    QuestionType,
)
from autoapply.profile.schema import (
    DateRange,
    Education,
    Experience,
    Profile,
    Skills,
)


# ============================================================================
# Classifier tests — 40+ paraphrases across 15+ QuestionTypes
# ============================================================================


# (raw_question, expected_type, optional_slot_dict_or_None)
CLASSIFIER_CASES: list[tuple[str, QuestionType, dict[str, str] | None]] = [
    # -- Work authorization ------------------------------------------------
    ("Are you legally authorized to work in the United States?", QuestionType.WORK_AUTHORIZED_US, None),
    ("Do you have work authorization in the US?", QuestionType.WORK_AUTHORIZED_US, None),
    ("Are you eligible to work in the U.S.?", QuestionType.WORK_AUTHORIZED_US, None),

    # Sponsorship — now
    ("Do you now or will you require visa sponsorship?", QuestionType.REQUIRE_SPONSORSHIP_NOW, None),
    ("Do you currently require sponsorship to work in the US?", QuestionType.REQUIRE_SPONSORSHIP_NOW, None),
    # Sponsorship — future
    ("Will you require sponsorship in the future?", QuestionType.REQUIRE_SPONSORSHIP_FUTURE, None),
    ("Do you anticipate needing sponsorship later on?", QuestionType.REQUIRE_SPONSORSHIP_FUTURE, None),

    # Visa / citizenship
    ("What is your current visa status?", QuestionType.VISA_STATUS, None),
    ("Are you a US citizen?", QuestionType.CITIZENSHIP, None),

    # -- YOE (with + without skill slot) -----------------------------------
    ("How many years of experience do you have with Python?", QuestionType.YOE_LANGUAGE, {"skill": "Python"}),
    ("Years of experience with C++?", QuestionType.YOE_LANGUAGE, {"skill": "C++"}),
    ("How many years of experience using Kubernetes?", QuestionType.YOE_LANGUAGE, {"skill": "Kubernetes"}),
    ("Years of relevant professional experience?", QuestionType.YOE_GENERAL, None),
    ("Total years of work experience?", QuestionType.YOE_GENERAL, None),

    # -- Contact ----------------------------------------------------------
    ("Email address", QuestionType.EMAIL, None),
    ("Your email", QuestionType.EMAIL, None),
    ("Phone number", QuestionType.PHONE, None),
    ("Mobile number", QuestionType.PHONE, None),
    ("LinkedIn URL", QuestionType.LINKEDIN_URL, None),
    ("LinkedIn profile", QuestionType.LINKEDIN_URL, None),
    ("GitHub profile", QuestionType.GITHUB_URL, None),
    ("Portfolio website", QuestionType.PORTFOLIO_URL, None),

    # -- Identity ---------------------------------------------------------
    ("First name", QuestionType.FIRST_NAME, None),
    ("Last name", QuestionType.LAST_NAME, None),
    ("Full legal name", QuestionType.FULL_NAME, None),
    ("Preferred name", QuestionType.PREFERRED_NAME, None),

    # -- Location --------------------------------------------------------
    ("Where are you currently located?", QuestionType.CURRENT_LOCATION, None),
    ("Current city", QuestionType.CURRENT_LOCATION, None),
    ("Are you willing to relocate?", QuestionType.WILLING_TO_RELOCATE, None),
    ("Open to relocation?", QuestionType.WILLING_TO_RELOCATE, None),

    # -- Availability ----------------------------------------------------
    ("What is your earliest start date?", QuestionType.AVAILABLE_START_DATE, None),
    ("When can you start?", QuestionType.AVAILABLE_START_DATE, None),

    # -- Education --------------------------------------------------------
    ("GPA", QuestionType.GPA, None),
    ("What is your cumulative GPA?", QuestionType.GPA, None),
    ("Expected graduation date", QuestionType.EXPECTED_GRADUATION, None),
    ("Anticipated graduation year", QuestionType.EXPECTED_GRADUATION, None),
    ("University", QuestionType.SCHOOL, None),
    ("What is your major?", QuestionType.MAJOR, None),

    # -- Compensation -----------------------------------------------------
    ("Salary expectation", QuestionType.SALARY_EXPECTATION, None),
    ("What are your salary requirements?", QuestionType.SALARY_EXPECTATION, None),
    ("Desired compensation", QuestionType.SALARY_EXPECTATION, None),

    # -- Demographics -----------------------------------------------------
    ("Gender", QuestionType.DEMO_GENDER, None),
    ("Race/ethnicity", QuestionType.DEMO_RACE, None),
    ("Are you Hispanic or Latino?", QuestionType.DEMO_HISPANIC_LATINO, None),
    ("Veteran status", QuestionType.DEMO_VETERAN, None),
    ("Do you have a disability?", QuestionType.DEMO_DISABILITY, None),
    ("Pronouns", QuestionType.DEMO_PRONOUNS, None),

    # -- Prior employment ------------------------------------------------
    ("Have you ever worked at our company?", QuestionType.PREVIOUSLY_EMPLOYED, None),
    ("Are you currently employed?", QuestionType.CURRENTLY_EMPLOYED, None),

    # -- Essays -----------------------------------------------------------
    ("Why do you want to work at our company?", QuestionType.WHY_COMPANY, None),
    ("Why are you interested in this role?", QuestionType.WHY_ROLE, None),
    ("Please provide a cover letter.", QuestionType.COVER_LETTER_BODY, None),

    # -- Referral ---------------------------------------------------------
    ("How did you hear about us?", QuestionType.HOW_HEARD_ABOUT, None),

    # -- Consents ---------------------------------------------------------
    ("Do you consent to a background check?", QuestionType.BACKGROUND_CHECK_CONSENT, None),
    ("I agree to the terms of service", QuestionType.AGREE_TO_TERMS, None),
]


@pytest.mark.parametrize("raw, expected_type, expected_slot", CLASSIFIER_CASES)
def test_classify_paraphrases(raw: str, expected_type: QuestionType, expected_slot):
    result = classify(raw)
    assert result.type is expected_type, (
        f"classify({raw!r}) -> {result.type.value} (expected {expected_type.value}); "
        f"match_text={result.match_text!r}"
    )
    assert result.confidence == 1.0
    assert result.source == "rule"
    if expected_slot is not None:
        for k, v in expected_slot.items():
            assert result.slot.get(k) == v, (
                f"slot mismatch for {raw!r}: got {result.slot}, want {expected_slot}"
            )


def test_classify_unknown_routes_to_unknown():
    """Truly novel question → UNKNOWN (which AnswerBank will route to review)."""
    result = classify("Please describe a time you navigated a vegetable emergency.")
    assert result.type is QuestionType.UNKNOWN
    assert result.confidence == 0.0


def test_classify_empty_input():
    assert classify("").type is QuestionType.UNKNOWN
    assert classify("    \t ").type is QuestionType.UNKNOWN


def test_classify_skill_aliases_canonicalize():
    """`py` → Python, `cpp` → C++, `k8s` → Kubernetes (via _canon_skill)."""
    assert classify("Years of experience in py?").slot["skill"] == "Python"
    assert classify("How many years of experience with cpp?").slot["skill"] == "C++"
    assert classify("Years of experience using k8s?").slot["skill"] == "Kubernetes"


# ============================================================================
# AnswerBank — policy routing
# ============================================================================


def _fake_profile() -> Profile:
    """Minimal Profile used across bank tests. Mirrors Aadit's real data."""
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
                minor="",
                gpa="3.975",
            )
        ],
        experiences=[
            Experience(
                title="SWE Intern",
                company="PayPal",
                stack=["Python", "AWS"],
                date_range=DateRange(
                    raw="May 2025 -- Aug 2025",
                    start=date(2025, 5, 1),
                    end=date(2025, 8, 1),
                ),
            )
        ],
        skills=Skills(languages=["Python", "C++"]),
        years_of_experience={"Python": 1.2, "AWS": 0.9, "C++": 0.5},
    )


def _bank() -> AnswerBank:
    return AnswerBank.from_path(
        Path(__file__).resolve().parents[1] / "state" / "answer_bank.yml"
    )


# -- Policy routing ---------------------------------------------------------


def test_review_required_types_route_to_review():
    bank = _bank()
    for qt in REVIEW_REQUIRED:
        q = classify_type(qt)
        ans = bank.answer(q, profile=_fake_profile(), track="swe")
        assert ans.requires_review, f"{qt.value} should route to review"
        assert ans.value is None


def test_llm_required_types_defer_to_llm():
    bank = _bank()
    for qt in LLM_REQUIRED:
        q = classify_type(qt)
        ans = bank.answer(q, profile=_fake_profile(), track="swe")
        assert ans.requires_llm, f"{qt.value} should defer to LLM"
        assert ans.value is None


def test_profile_sourced_resolves_from_profile():
    bank = _bank()
    p = _fake_profile()

    ans = bank.answer(classify_type(QuestionType.EMAIL), profile=p, track="swe")
    assert ans.value == "aaditnilay@gmail.com"
    assert ans.source == "profile"

    assert bank.answer(classify_type(QuestionType.LINKEDIN_URL), profile=p, track="swe").value.startswith("https://")
    assert bank.answer(classify_type(QuestionType.GPA), profile=p, track="swe").value == "3.975"
    assert bank.answer(classify_type(QuestionType.SCHOOL), profile=p, track="swe").value.startswith("University of Maryland")
    assert bank.answer(classify_type(QuestionType.DEGREE), profile=p, track="swe").value.startswith("B.S.")
    assert bank.answer(classify_type(QuestionType.FIRST_NAME), profile=p, track="swe").value == "Aadit"
    assert bank.answer(classify_type(QuestionType.LAST_NAME), profile=p, track="swe").value == "Nilay"


def test_profile_sourced_without_profile_routes_to_review():
    bank = _bank()
    ans = bank.answer(classify_type(QuestionType.EMAIL), profile=None, track="swe")
    assert ans.requires_review
    assert "profile missing" in ans.notes


def test_graduation_date_formatted():
    bank = _bank()
    ans = bank.answer(classify_type(QuestionType.GRADUATION_DATE), profile=_fake_profile(), track="swe")
    assert ans.value == "May 2026"


def test_yoe_language_uses_profile_yoe_dict():
    bank = _bank()
    p = _fake_profile()
    cq = classify("How many years of experience do you have with Python?")
    ans = bank.answer(cq, profile=p, track="swe")
    assert ans.value == "1.2"  # 0.2 > 0.1 tolerance → formatted with decimal
    assert ans.source == "profile"

    # Skill not on resume → "0" (forms need a numeric answer; missing is wrong)
    cq2 = classify("Years of experience in Fortran?")
    ans2 = bank.answer(cq2, profile=p, track="swe")
    assert ans2.value == "0"


def test_yoe_language_is_case_insensitive():
    bank = _bank()
    # Profile uses title-case "Python"; the question uses "python". The
    # classifier canonicalizes via _SKILL_ALIASES; _from_profile then also
    # falls back to case-insensitive dict lookup.
    p = _fake_profile()
    p.years_of_experience = {"Python": 2.6}
    cq = classify("How many years of experience with python?")
    ans = bank.answer(cq, profile=p, track="swe")
    assert ans.value == "2.6"  # 0.6 > 0.1 tolerance → formatted with decimal


# -- Bank lookup semantics --------------------------------------------------


def test_bank_per_track_beats_default():
    bank = _bank()
    p = _fake_profile()
    cq = classify("Why are you interested in this role?")
    ans = bank.answer(cq, profile=p, track="quant")
    assert ans.source == "bank"
    assert "quant" in ans.value.lower() or "trading" in ans.value.lower()


def test_bank_default_fallback_when_no_track_override():
    bank = _bank()
    p = _fake_profile()
    cq = classify("Where are you currently located?")
    ans = bank.answer(cq, profile=p, track="hpc")
    assert ans.source == "bank:_default"
    assert ans.value == "College Park, MD"


def test_bank_missing_entry_routes_to_review():
    # Type that is neither profile-sourced nor LLM/review, and has no bank entry.
    # We'll construct a synthetic bank with one entry missing.
    bank = AnswerBank.from_dict({
        "work_authorized_us": {"_default": "Yes"},
        # salary_expectation intentionally omitted
    })
    cq = classify("Salary expectation")
    ans = bank.answer(cq, profile=_fake_profile(), track="swe")
    assert ans.requires_review
    assert "no bank entry" in ans.notes


def test_bank_raw_resolution_end_to_end():
    bank = _bank()
    p = _fake_profile()
    ans = bank.resolve_raw("Are you authorized to work in the US?", profile=p, track="swe")
    assert ans.value == "Yes"
    assert ans.source == "bank:_default"


def test_bank_why_company_is_llm_routed():
    """why_company is in LLM_REQUIRED — bank must NOT answer it even though we have material."""
    bank = _bank()
    ans = bank.resolve_raw("Why do you want to work at our company?", profile=_fake_profile(), track="swe")
    assert ans.requires_llm
    assert ans.value is None


def test_bank_from_path_missing_raises():
    with pytest.raises(FileNotFoundError):
        AnswerBank.from_path(Path("/nonexistent/answer_bank.yml"))


# -- Helpers ----------------------------------------------------------------


def test_format_yoe_rounding():
    assert _format_yoe(0.0) == "0"
    assert _format_yoe(0.3) == "1"   # under a year, positive → 1
    assert _format_yoe(1.0) == "1"
    assert _format_yoe(1.05) == "1"
    assert _format_yoe(1.5) == "1.5"
    assert _format_yoe(3.0) == "3"
    assert _format_yoe(3.95) == "4"  # within 0.1 tolerance of 4


def test_extract_major():
    assert _extract_major("B.S. Computer Science and Mathematics") == "Computer Science and Mathematics"
    assert _extract_major("Bachelor of Science in Computer Science") == "Computer Science"
    assert _extract_major("Computer Science") == "Computer Science"  # unchanged
    assert _extract_major("M.S. in Applied Math") == "Applied Math"


# ============================================================================
# Seed bank sanity checks — catches accidental YAML breakage
# ============================================================================


def test_seed_bank_has_coverage_for_all_bank_routed_types():
    """Every QuestionType that is NOT profile-sourced, LLM-required, or review-required
    should have an entry in the seed bank (otherwise the form will silently go to review)."""
    bank = _bank()
    skipped = PROFILE_SOURCED | LLM_REQUIRED | REVIEW_REQUIRED
    missing: list[str] = []
    for qt in QuestionType:
        if qt in skipped:
            continue
        val, src = bank.lookup(qt, "swe")
        if val is None:
            missing.append(qt.value)
    assert not missing, f"seed bank missing entries for: {missing}"


def test_seed_bank_why_role_has_all_tracks():
    bank = _bank()
    for track in ("swe", "ml", "hpc", "quant"):
        val, src = bank.lookup(QuestionType.WHY_ROLE, track)
        assert val is not None, f"why_role missing for track={track}"
        assert src == "bank", f"why_role track={track} fell back to _default (expected per-track)"


# ============================================================================
# Private helpers for these tests
# ============================================================================


def classify_type(qt: QuestionType):
    """Build a minimal ClassifiedQuestion for a specific type without going
    through the rule table — used to isolate routing tests from classifier bugs."""
    from autoapply.answers.classifier import ClassifiedQuestion

    return ClassifiedQuestion(
        type=qt,
        confidence=1.0,
        original=f"<synthetic {qt.value}>",
        source="rule",
    )
