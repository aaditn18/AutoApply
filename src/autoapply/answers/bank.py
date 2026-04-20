"""Deterministic answer resolution.

Routing priority:
  1. PROFILE_SOURCED types → pulled from `Profile` (resume facts).
  2. LLM_REQUIRED types    → deferred to the LLM stage (e.g. why_company).
  3. REVIEW_REQUIRED types  → routed to the human review queue.
  4. Otherwise              → looked up in `state/answer_bank.yml` keyed by
                              `(question_type, resume_track)` with a `_default`
                              fallback per question_type.

This module owns ~95% of screening-form traffic. The LLM never touches an
answer returned from here — see the policy frozensets in `answers.types`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from autoapply.answers.classifier import ClassifiedQuestion, classify
from autoapply.answers.types import (
    LLM_REQUIRED,
    PROFILE_SOURCED,
    REVIEW_REQUIRED,
    QuestionType,
)
from autoapply.profile.schema import Profile


# -- Public data structure ---------------------------------------------------


@dataclass
class Answer:
    """Resolved answer for a single classified question.

    Exactly one of `value` / `requires_llm` / `requires_review` will be the
    actionable signal. The executor inspects flags in that order:
      - requires_review=True → route the whole application to the review queue
      - requires_llm=True    → route to the cover-letter / essay generator
      - value set            → autofill form field
    """

    question_type: QuestionType
    value: str | None = None
    requires_llm: bool = False
    requires_review: bool = False
    source: str = ""           # "profile" | "bank" | "bank:_default" | "review" | "llm" | "missing"
    slot: dict[str, str] = field(default_factory=dict)
    notes: str = ""


# -- Bank loader -------------------------------------------------------------


class AnswerBank:
    """Loads `state/answer_bank.yml` and resolves answers."""

    def __init__(self, data: dict[str, Any]):
        # {question_type_value: {track_or_"_default": "<answer string>"}}
        self._data = data

    # ---- loading --------------------------------------------------------

    @classmethod
    def from_path(cls, path: Path) -> "AnswerBank":
        if not path.exists():
            raise FileNotFoundError(f"answer bank not found at {path}")
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"answer bank at {path} must be a top-level mapping")
        return cls(raw)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AnswerBank":
        return cls(data)

    # ---- lookup ---------------------------------------------------------

    def lookup(self, qt: QuestionType, track: str) -> tuple[str | None, str]:
        """Return ``(value, source)``.

        ``source`` is one of ``"bank"`` (per-track hit), ``"bank:_default"``
        (default-fallback hit), or ``"missing"`` (no entry at all).

        An explicitly empty bank value (``_default: ""``) is a VALID answer
        meaning "intentionally leave this field blank" — e.g. the referral
        name/email fields that we submit empty when we don't have a referral.
        Only ``None`` and missing keys are treated as "not set".
        """
        entry = self._data.get(qt.value)
        if not isinstance(entry, dict):
            return None, "missing"
        # Per-track takes precedence over _default.
        if track in entry and entry[track] is not None:
            return str(entry[track]), "bank"
        if "_default" in entry and entry["_default"] is not None:
            return str(entry["_default"]), "bank:_default"
        return None, "missing"

    # ---- main entry point ----------------------------------------------

    def answer(
        self,
        q: ClassifiedQuestion,
        *,
        profile: Profile | None = None,
        track: str = "swe",
    ) -> Answer:
        """Resolve a classified question to an `Answer`.

        Contract:
          * never calls an LLM directly
          * never invents values — if data is missing, returns `requires_review=True`
          * honors policy frozensets from `answers.types`
        """
        qt = q.type

        # 1. Unknown / review-required types always route to review.
        if qt in REVIEW_REQUIRED:
            return Answer(
                question_type=qt,
                requires_review=True,
                source="review",
                slot=q.slot,
                notes="review-required question type",
            )

        # 2. LLM-required types (why_company, cover_letter_body) are deferred.
        if qt in LLM_REQUIRED:
            return Answer(
                question_type=qt,
                requires_llm=True,
                source="llm",
                slot=q.slot,
                notes="LLM-required question type (per-job)",
            )

        # 3. Profile-sourced types — pull deterministic values from the resume.
        if qt in PROFILE_SOURCED:
            if profile is None:
                return Answer(
                    question_type=qt,
                    requires_review=True,
                    source="missing",
                    slot=q.slot,
                    notes="profile missing; cannot resolve profile-sourced type",
                )
            val = _from_profile(qt, profile, q.slot)
            if val is None or val == "":
                return Answer(
                    question_type=qt,
                    requires_review=True,
                    source="missing",
                    slot=q.slot,
                    notes=f"profile has no value for {qt.value}",
                )
            return Answer(
                question_type=qt,
                value=val,
                source="profile",
                slot=q.slot,
            )

        # 4. Bank lookup with per-track -> _default fallback.
        val, src = self.lookup(qt, track)
        if val is None:
            return Answer(
                question_type=qt,
                requires_review=True,
                source="missing",
                slot=q.slot,
                notes=f"no bank entry for {qt.value} (track={track})",
            )
        return Answer(
            question_type=qt,
            value=val,
            source=src,
            slot=q.slot,
        )

    # ---- convenience ----------------------------------------------------

    def resolve_raw(
        self,
        raw_question: str,
        *,
        profile: Profile | None = None,
        track: str = "swe",
    ) -> Answer:
        """Classify + resolve in one shot. Useful for tests and quick scripts."""
        return self.answer(classify(raw_question), profile=profile, track=track)


# -- Profile-sourced resolution ---------------------------------------------


# Simple QuestionType → Profile-attribute lookups. Every entry resolves
# via ``getattr(profile, attr, None) or None`` — falsy values (empty
# string, etc.) return None so the bank's caller can decide the policy.
#
# Adding a new PROFILE_SOURCED type: add both the enum entry and
# one line here. Non-trivial lookups (names, education, YOE) have
# dedicated handlers below.
_SIMPLE_PROFILE_ATTRS: dict[QuestionType, str] = {
    # EEO / demographic
    QuestionType.DEMO_GENDER: "demo_gender",
    QuestionType.DEMO_RACE: "demo_race",
    QuestionType.DEMO_HISPANIC_LATINO: "demo_hispanic_latino",
    QuestionType.DEMO_VETERAN: "demo_veteran",
    QuestionType.DEMO_DISABILITY: "demo_disability",
    QuestionType.DEMO_PRONOUNS: "demo_pronouns",
    QuestionType.DEMO_SEXUAL_ORIENTATION: "demo_sexual_orientation",
    QuestionType.DEMO_TRANSGENDER: "demo_transgender",
    # Military / prior service
    QuestionType.MILITARY_SERVICE: "military_service",
    # Citizenship / immigration
    QuestionType.CITIZENSHIP: "citizenship_country",
    QuestionType.US_CITIZEN: "us_citizen",
    QuestionType.WORK_AUTHORIZED_US: "work_authorized_us",
    QuestionType.PERMANENT_WORK_AUTHORIZATION: "permanent_work_authorization",
    QuestionType.REQUIRE_SPONSORSHIP_NOW: "require_sponsorship_now",
    QuestionType.REQUIRE_SPONSORSHIP_FUTURE: "require_sponsorship_future",
    QuestionType.VISA_STATUS: "visa_status",
    # Current location atoms
    QuestionType.CURRENT_CITY: "current_city",
    QuestionType.CURRENT_STATE: "current_state",
    QuestionType.CURRENT_ZIP: "current_zip",
    QuestionType.CURRENT_LOCATION: "current_location",
    # Contact
    QuestionType.EMAIL: "email",
    QuestionType.PHONE: "phone",
    QuestionType.LINKEDIN_URL: "linkedin_url",
    QuestionType.GITHUB_URL: "github_url",
}


# Types handled by the non-trivial handlers below. Never in
# ``_SIMPLE_PROFILE_ATTRS`` — the dispatch order matters.
_NAME_QUESTION_TYPES = frozenset({
    QuestionType.FULL_NAME,
    QuestionType.FIRST_NAME,
    QuestionType.LAST_NAME,
    QuestionType.PREFERRED_NAME,
})

_EDUCATION_QUESTION_TYPES = frozenset({
    QuestionType.SCHOOL,
    QuestionType.DEGREE,
    QuestionType.MAJOR,
    QuestionType.MINOR,
    QuestionType.GPA,
    QuestionType.GRADUATION_DATE,
    QuestionType.EXPECTED_GRADUATION,
})


# Types we know about but have no profile source for — returning None
# here routes the caller to the review / LLM path rather than falsely
# claiming we don't know about the type.
_KNOWN_NULL_TYPES = frozenset({
    QuestionType.PORTFOLIO_URL,  # not tracked on Profile yet
    QuestionType.WEBSITE_URL,
})


def _from_profile(
    qt: QuestionType, profile: Profile, slot: dict[str, str],
) -> str | None:
    """Map a PROFILE_SOURCED QuestionType to a string from the parsed resume.

    Simple attribute-copy types are handled via :data:`_SIMPLE_PROFILE_ATTRS`
    (one line per new type). Types with non-trivial extraction (splitting
    names, formatting graduation dates, skill-keyed YOE lookup) have
    their own handlers.
    """
    # 1. Identity — split full_name according to which slot was asked.
    if qt in _NAME_QUESTION_TYPES:
        return _name_from_profile(qt, profile)

    # 2. Education — first entry wins (UMD for Aadit).
    if qt in _EDUCATION_QUESTION_TYPES:
        return _education_from_profile(qt, profile)

    # 3. YOE for a specific skill (slot["skill"]).
    if qt is QuestionType.YOE_LANGUAGE:
        return _yoe_from_profile(profile, slot)

    # 4. Known-null types — not on Profile, deliberate.
    if qt in _KNOWN_NULL_TYPES:
        return None

    # 5. Simple attribute lookup.
    attr = _SIMPLE_PROFILE_ATTRS.get(qt)
    if attr is None:
        return None
    return getattr(profile, attr, None) or None


def _name_from_profile(qt: QuestionType, profile: Profile) -> str | None:
    """Extract first/last/full/preferred from ``profile.full_name``.

    Preferred-name falls back to first name since we don't track an
    explicit preferred-name field yet.
    """
    name = profile.full_name or ""
    if qt is QuestionType.FULL_NAME:
        return name or None
    if not name:
        return None
    if qt is QuestionType.FIRST_NAME or qt is QuestionType.PREFERRED_NAME:
        return name.split(" ", 1)[0]
    if qt is QuestionType.LAST_NAME:
        parts = name.rsplit(" ", 1)
        return parts[1] if len(parts) == 2 else None
    return None


def _education_from_profile(qt: QuestionType, profile: Profile) -> str | None:
    """Read an education-backed field from ``profile.education[0]``."""
    edu = profile.education[0] if profile.education else None
    if edu is None:
        return None
    if qt is QuestionType.SCHOOL:
        return edu.school or None
    if qt is QuestionType.DEGREE:
        return edu.degree or None
    if qt is QuestionType.MAJOR:
        return _extract_major(edu.degree)
    if qt is QuestionType.MINOR:
        return edu.minor or None
    if qt is QuestionType.GPA:
        return edu.gpa or None
    if qt is QuestionType.GRADUATION_DATE or qt is QuestionType.EXPECTED_GRADUATION:
        # Prefer the end date (or start if no end) as "Month YYYY".
        dr = edu.date_range
        if dr.end is not None:
            return dr.end.strftime("%B %Y")
        if dr.is_present and dr.start is not None:
            return dr.start.strftime("%B %Y")
        return dr.raw or None
    return None


def _yoe_from_profile(profile: Profile, slot: dict[str, str]) -> str | None:
    """Look up years-of-experience for ``slot["skill"]``.

    Returns "0" (not None) when the skill isn't on the resume — most ATS
    forms reject an empty numeric answer, so an explicit zero is better
    than a blank.
    """
    skill = slot.get("skill", "")
    if not skill:
        return None
    yoe = profile.years_of_experience or {}
    # Exact match.
    if skill in yoe:
        return _format_yoe(yoe[skill])
    # Case-insensitive match.
    for k, v in yoe.items():
        if k.lower() == skill.lower():
            return _format_yoe(v)
    return "0"


def _format_yoe(years: float) -> str:
    """Human-readable YOE: '3' not '3.0', '1.5' keeps the fractional part."""
    if years >= 1:
        rounded = round(years)
        if abs(years - rounded) < 0.1:
            return str(rounded)
        return f"{years:.1f}"
    # Under a year — round up to 1 for form purposes (most forms use int years).
    return "1" if years > 0 else "0"


_MAJOR_STRIP_PREFIX = (
    "bachelor of science in ",
    "b.s. in ",
    "bs in ",
    "b.s. ",
    "bs ",
    "bachelor of ",
    "master of science in ",
    "m.s. in ",
    "ms in ",
    "master of ",
)


def _extract_major(degree: str) -> str:
    """Best-effort 'B.S. Computer Science and Mathematics' -> 'Computer Science and Mathematics'."""
    d = degree.strip()
    low = d.lower()
    for p in _MAJOR_STRIP_PREFIX:
        if low.startswith(p):
            return d[len(p):].strip(" ,.;")
    return d


# -- Convenience load --------------------------------------------------------


def load_default_bank() -> AnswerBank:
    """Load `state/answer_bank.yml` at the path dictated by `Settings`."""
    from autoapply.config import get_settings

    return AnswerBank.from_path(get_settings().answer_bank_path)
