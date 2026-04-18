"""Map ATS field labels to a resolved answer value.

Greenhouse and Lever expose form metadata via their public APIs. Each
field has:
- `name` or `id` — machine key (e.g., `first_name`, `resume`, `cover_letter`,
  `urls[linkedin]`, `custom_fields[...]`)
- `label` — human-readable label we classify against.

Resolution priority:
    1. Machine-key exact match (first_name, last_name, email, phone, etc.)
    2. Question-type classifier over `label` → AnswerBank / Profile

If a field is required and no answer can be produced → raise
`UnresolvedField`, which the applicator catches to route the job to the
review queue (never submit a blank required field).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Callable

from autoapply.answers import classifier as classifier_mod
from autoapply.answers.bank import AnswerBank
from autoapply.answers.types import PROFILE_SOURCED, QuestionType
from autoapply.profile.schema import Profile


log = logging.getLogger(__name__)


ClassifyFn = Callable[[str], classifier_mod.ClassifiedQuestion]


class UnresolvedField(Exception):
    """Raised when a required field has no deterministic answer."""

    def __init__(self, label: str, name: str = "", reason: str = ""):
        self.label = label
        self.name = name
        self.reason = reason
        super().__init__(f"UnresolvedField label={label!r} name={name!r} reason={reason}")


@dataclass
class ResolvedField:
    name: str
    label: str
    value: str
    source: str  # "machine_key" | "profile" | "bank" | "classifier+bank" | "llm_required" | "review_required" | "none"
    question_type: QuestionType | None = None
    requires_llm: bool = False
    requires_review: bool = False


@dataclass
class FieldSpec:
    """A single ATS form field as reported by the board API."""

    name: str
    label: str
    required: bool = False
    kind: str = "text"  # text | textarea | select | file | multi_select | checkbox
    options: list[str] = field(default_factory=list)


# -- Machine-key → profile attribute -----------------------------------------


_MACHINE_KEY_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^first[_\-]?name$", re.I), "first_name"),
    (re.compile(r"^last[_\-]?name$", re.I), "last_name"),
    (re.compile(r"^full[_\-]?name$", re.I), "full_name"),
    (re.compile(r"^name$", re.I), "full_name"),
    (re.compile(r"^email(?:[_\-]?address)?$", re.I), "email"),
    (re.compile(r"^phone(?:[_\-]?number)?$", re.I), "phone"),
    (re.compile(r"^resume(?:_file|_text|_doc|_upload)?$", re.I), "resume"),
    (re.compile(r"^cv$", re.I), "resume"),
    (re.compile(r"^cover[_\-]?letter$", re.I), "cover_letter"),
    (re.compile(r"linkedin", re.I), "linkedin_url"),
    (re.compile(r"github", re.I), "github_url"),
    (re.compile(r"portfolio|website", re.I), "website_url"),
    (re.compile(r"location|city", re.I), "location"),
]


def _match_machine_key(name: str) -> str | None:
    for pat, attr in _MACHINE_KEY_RULES:
        if pat.search(name or ""):
            return attr
    return None


def _profile_value(attr: str, profile: Profile) -> str | None:
    if attr == "full_name":
        return profile.full_name or None
    if attr == "first_name":
        parts = (profile.full_name or "").split(None, 1)
        return parts[0] if parts else None
    if attr == "last_name":
        parts = (profile.full_name or "").split(None, 1)
        return parts[1] if len(parts) > 1 else None
    if attr == "email":
        return profile.email or None
    if attr == "phone":
        return profile.phone or None
    if attr == "linkedin_url":
        return profile.linkedin_url or None
    if attr == "github_url":
        return profile.github_url or None
    # website_url / location aren't on Profile → fall back to classifier path.
    return None


# -- Resolve a single field --------------------------------------------------


def resolve_field(
    spec: FieldSpec,
    *,
    profile: Profile,
    bank: AnswerBank,
    track: str,
    classify_fn: ClassifyFn = classifier_mod.classify,
    cover_letter_text: str | None = None,
    resume_path: str | None = None,
) -> ResolvedField:
    """Resolve one field deterministically. Raises UnresolvedField if required
    and no answer can be produced."""
    # 1. File uploads — handled separately (path injected by caller).
    if spec.kind == "file":
        attr = _match_machine_key(spec.name) or ""
        if attr == "resume":
            if resume_path:
                return ResolvedField(spec.name, spec.label, resume_path, "machine_key")
            if spec.required:
                raise UnresolvedField(spec.label, spec.name, "no resume path")
            return ResolvedField(spec.name, spec.label, "", "machine_key")
        if attr == "cover_letter":
            return ResolvedField(
                spec.name, spec.label, cover_letter_text or "", "machine_key"
            )
        if spec.required:
            raise UnresolvedField(spec.label, spec.name, "unknown file field")
        return ResolvedField(spec.name, spec.label, "", "machine_key")

    # 2. Machine-key direct mapping.
    attr = _match_machine_key(spec.name)
    if attr == "cover_letter":
        return ResolvedField(
            spec.name, spec.label, cover_letter_text or "", "machine_key"
        )
    # Text-area variant of resume upload (e.g. Greenhouse `resume_text`).
    # The actual PDF is submitted via the file-upload input; the text-paste
    # variant is redundant and is NOT rendered by the new job-boards.greenhouse.io
    # SPA.  Return empty value so build_payload's `if r.value:` gate silently
    # skips this field — prevents a spurious fill:resume_text:ValueError in
    # field_errors when the DOM element doesn't exist.
    if attr == "resume" and spec.kind in ("text", "textarea"):
        return ResolvedField(spec.name, spec.label, "", "machine_key")
    if attr:
        val = _profile_value(attr, profile)
        if val is not None and val != "":
            return ResolvedField(
                spec.name, spec.label, _snap_to_option(val, spec), "machine_key"
            )

    # 3. Classifier → bank/profile.
    classified = classify_fn(spec.label)
    qt = classified.type
    if qt is not QuestionType.UNKNOWN:
        answer = bank.answer(classified, profile=profile, track=track)
        # Optional fields with no deterministic answer → leave blank; we do
        # NOT block the whole application on an optional review-required
        # portfolio URL or optional why-company essay. Required fields
        # propagate the review/llm flag so the caller can route the app.
        if answer.requires_review:
            if spec.required:
                return ResolvedField(
                    spec.name,
                    spec.label,
                    answer.value or "",
                    "review_required",
                    question_type=qt,
                    requires_review=True,
                )
            return ResolvedField(spec.name, spec.label, "", "none", question_type=qt)
        if answer.requires_llm:
            if spec.required:
                return ResolvedField(
                    spec.name,
                    spec.label,
                    answer.value or "",
                    "llm_required",
                    question_type=qt,
                    requires_llm=True,
                )
            return ResolvedField(spec.name, spec.label, "", "none", question_type=qt)
        if answer.value:
            src = "profile" if qt in PROFILE_SOURCED else "classifier+bank"
            return ResolvedField(
                spec.name,
                spec.label,
                _snap_to_option(answer.value, spec),
                src,
                question_type=qt,
            )

    # 3.5 LLM / template fallback for genuinely novel text fields.
    # Reached only when the classifier returns UNKNOWN (step 3 found no type).
    # For text/textarea: Gemini Flash drafts an answer from the profile context.
    # For select/multi_select: same, but constrained to the option list.
    # Marked source="llm_answer" for audit logging.
    # Does NOT set requires_review — caller auto-submits and the answer is
    # visible in the Application.answers audit column.
    if spec.kind in ("text", "textarea", "select", "multi_select"):
        try:
            from autoapply.answers.llm_fallback import draft_field_answer

            ans = draft_field_answer(
                label=spec.label, spec=spec, profile=profile, track=track
            )
            if ans:
                return ResolvedField(
                    spec.name,
                    spec.label,
                    _snap_to_option(ans, spec),
                    "llm_answer",
                )
        except Exception as exc:
            log.debug("llm_fallback error for label=%r: %s", spec.label, exc)

    # 4. Give up. Required → raise; optional → empty string.
    if spec.required:
        raise UnresolvedField(spec.label, spec.name, "no classifier hit, no bank entry")
    return ResolvedField(spec.name, spec.label, "", "none")


def _snap_to_option(value: str, spec: FieldSpec) -> str:
    """For select/multi_select fields, snap value to the closest literal option.

    Case-insensitive exact match first, then substring match. For US-person-
    eligibility selects (ITAR, export-control forms) where the resolved value
    is a non-US country name, falls back to the 'Not currently / Other status'
    option. Leaves value unchanged if no option matches — the server will
    reject it, which we surface as a form error upstream.
    """
    if spec.kind not in ("select", "multi_select", "checkbox"):
        return value
    if not spec.options:
        return value
    v = value.strip().lower()
    for opt in spec.options:
        if opt.strip().lower() == v:
            return opt
    for opt in spec.options:
        if v in opt.lower() or opt.lower() in v:
            return opt

    # When no option matches: detect US-person-eligibility select fields
    # (ITAR / export-control forms) and pick the appropriate "other" option.
    # These selects list US immigration statuses; a non-US country name like
    # "India" won't substring-match any of them, so we fall back explicitly.
    _US_PERSON_MARKERS = (
        "u.s. citizen", "u.s. national", "united states citizen",
        "green card", "lawful permanent", "lawfully admitted",
        "refugee", "asylee", "8 u.s.c",
    )
    has_us_options = any(
        any(m in opt.lower() for m in _US_PERSON_MARKERS)
        for opt in spec.options
    )
    if has_us_options:
        _OTHER_MARKERS = (
            "not currently", "other status", "not a u.s.", "not a us",
            "other", "not yet", "none of the above",
        )
        for opt in spec.options:
            ol = opt.lower()
            if any(m in ol for m in _OTHER_MARKERS):
                return opt

    return value


def resolve_all(
    specs: list[FieldSpec],
    *,
    profile: Profile,
    bank: AnswerBank,
    track: str,
    classify_fn: ClassifyFn = classifier_mod.classify,
    cover_letter_text: str | None = None,
    resume_path: str | None = None,
) -> tuple[list[ResolvedField], list[UnresolvedField]]:
    """Resolve a whole form. Returns (resolved, unresolved_exceptions).
    Even with unresolved required fields, we return all successfully-resolved
    answers so the caller can build the review-queue payload."""
    resolved: list[ResolvedField] = []
    unresolved: list[UnresolvedField] = []
    for spec in specs:
        try:
            resolved.append(
                resolve_field(
                    spec,
                    profile=profile,
                    bank=bank,
                    track=track,
                    classify_fn=classify_fn,
                    cover_letter_text=cover_letter_text,
                    resume_path=resume_path,
                )
            )
        except UnresolvedField as exc:
            unresolved.append(exc)
    return resolved, unresolved
