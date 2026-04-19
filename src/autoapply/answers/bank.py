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


def _from_profile(qt: QuestionType, profile: Profile, slot: dict[str, str]) -> str | None:
    """Map a PROFILE_SOURCED QuestionType to a string from the parsed resume."""
    # Identity
    if qt is QuestionType.FULL_NAME:
        return profile.full_name
    if qt is QuestionType.FIRST_NAME:
        return profile.full_name.split(" ", 1)[0] if profile.full_name else None
    if qt is QuestionType.LAST_NAME:
        parts = profile.full_name.rsplit(" ", 1)
        return parts[1] if len(parts) == 2 else None
    if qt is QuestionType.PREFERRED_NAME:
        # Fall back to first name if no explicit preferred name.
        return profile.full_name.split(" ", 1)[0] if profile.full_name else None

    # Contact
    if qt is QuestionType.EMAIL:
        return profile.email or None
    if qt is QuestionType.PHONE:
        return profile.phone or None
    if qt is QuestionType.LINKEDIN_URL:
        return profile.linkedin_url or None
    if qt is QuestionType.GITHUB_URL:
        return profile.github_url or None
    if qt is QuestionType.PORTFOLIO_URL:
        # Not tracked on Profile yet — route to review.
        return None
    if qt is QuestionType.WEBSITE_URL:
        return None

    # Education (first entry wins — UMD for Aadit)
    edu = profile.education[0] if profile.education else None
    if qt is QuestionType.SCHOOL:
        return edu.school if edu else None
    if qt is QuestionType.DEGREE:
        return edu.degree if edu else None
    if qt is QuestionType.MAJOR:
        return _extract_major(edu.degree) if edu else None
    if qt is QuestionType.MINOR:
        return edu.minor or None if edu else None
    if qt is QuestionType.GPA:
        return edu.gpa or None if edu else None
    if qt is QuestionType.GRADUATION_DATE or qt is QuestionType.EXPECTED_GRADUATION:
        if not edu:
            return None
        # Prefer the end date (or start if no end) formatted as "Month YYYY".
        dr = edu.date_range
        if dr.end is not None:
            return dr.end.strftime("%B %Y")
        if dr.is_present and dr.start is not None:
            return dr.start.strftime("%B %Y")
        return dr.raw or None

    # YOE for a specific skill
    if qt is QuestionType.YOE_LANGUAGE:
        skill = slot.get("skill", "")
        if not skill:
            return None
        yoe = profile.years_of_experience or {}
        # Try exact, then case-insensitive.
        if skill in yoe:
            return _format_yoe(yoe[skill])
        for k, v in yoe.items():
            if k.lower() == skill.lower():
                return _format_yoe(v)
        # Not present on this resume → explicitly "0" rather than missing, so
        # the form gets a valid numeric answer. (Many ATS forms reject empty.)
        return "0"

    return None


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
