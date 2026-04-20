"""Phase 1 — deterministic per-field resolution.

For each ``FieldSpec`` we try, in order:

  1. **File uploads** — resume / cover_letter paths come from the
     applicator. Unknown file fields raise UnresolvedField if required.
  2. **Machine-key direct map** — if the field name matches a known
     pattern (first_name, email, phone, linkedin_url, ...), read from
     :class:`Profile` via :func:`.machine_key._profile_value`.
  3. **resume_text textarea** — when the tenant wants plaintext resume
     content in a textarea, extract from the same ``.tex`` source the
     PDF was compiled from (no LLM round-trip).
  4. **Classifier → bank / profile** — run the label through the
     classifier and look up the resulting QuestionType in the
     :class:`AnswerBank`.
  5. **Per-field LLM fallback (select / multi_select only)** — only
     fires for dropdown picks where the LLM needs to reason about the
     options list at resolver time. Text/textarea fields defer to the
     batched LLM which gets full JD context.

Returns a :class:`ResolvedField` for every spec, and the set of
``UnresolvedField`` exceptions for required fields that couldn't be
answered — the caller routes the job to the review queue (never
submits a blank required field).
"""

from __future__ import annotations

import logging

from autoapply.answers import classifier as classifier_mod
from autoapply.answers.bank import AnswerBank
from autoapply.answers.types import PROFILE_SOURCED, QuestionType
from autoapply.profile.schema import Profile

# Import from the public standard_fields module so we share the exact
# same dataclass types callers expect.
from autoapply.execute.standard_fields import (
    ClassifyFn,
    FieldSpec,
    ResolvedField,
    UnresolvedField,
)

from .machine_key import _match_machine_key, _profile_value
from .options_snap import _snap_to_option


log = logging.getLogger(__name__)


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
    """Resolve one field deterministically.

    Raises :class:`UnresolvedField` if required and no answer can be
    produced. Optional fields with no answer return an empty-value
    ResolvedField so the caller can still populate a payload row.
    """
    # 1. File uploads.
    if spec.kind == "file":
        return _resolve_file(spec, cover_letter_text, resume_path)

    # 2. Machine-key direct mapping.
    attr = _match_machine_key(spec.name)
    if attr == "cover_letter":
        return ResolvedField(
            spec.name, spec.label, cover_letter_text or "", "machine_key"
        )
    # 3. resume_text textarea — extract from .tex source.
    if attr == "resume" and spec.kind in ("text", "textarea"):
        return _resolve_resume_text(spec, track)
    if attr:
        val = _profile_value(attr, profile)
        if val is not None and val != "":
            return ResolvedField(
                spec.name, spec.label, _snap_to_option(val, spec), "machine_key"
            )

    # 4. Classifier → bank/profile.
    classified = classify_fn(spec.label)
    qt = classified.type
    if qt is not QuestionType.UNKNOWN:
        answer = bank.answer(classified, profile=profile, track=track)
        # Optional fields with no deterministic answer → leave blank.
        # Required fields propagate the review/llm flag so the caller
        # can route the app.
        if answer.requires_review:
            if spec.required:
                return ResolvedField(
                    spec.name, spec.label, answer.value or "",
                    "review_required", question_type=qt, requires_review=True,
                )
            return ResolvedField(spec.name, spec.label, "", "none", question_type=qt)
        if answer.requires_llm:
            if spec.required:
                return ResolvedField(
                    spec.name, spec.label, answer.value or "",
                    "llm_required", question_type=qt, requires_llm=True,
                )
            return ResolvedField(spec.name, spec.label, "", "none", question_type=qt)
        if answer.value:
            src = "profile" if qt in PROFILE_SOURCED else "classifier+bank"
            return ResolvedField(
                spec.name, spec.label,
                _snap_to_option(answer.value, spec), src,
                question_type=qt,
            )

    # 5. Per-field LLM fallback — select / multi_select only. Text /
    # textarea kinds DEFER to the batched LLM resolver which gets the
    # full job description as context; using draft_field_answer here
    # would generate a generic answer WITHOUT that context.
    if spec.kind in ("select", "multi_select"):
        ans = _try_per_field_llm(spec, profile, track)
        if ans:
            return ResolvedField(
                spec.name, spec.label, _snap_to_option(ans, spec), "llm_answer",
            )

    # 6. Give up.
    if spec.required:
        raise UnresolvedField(
            spec.label, spec.name, "no classifier hit, no bank entry"
        )
    return ResolvedField(spec.name, spec.label, "", "none")


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
    """Resolve a whole form. Returns ``(resolved, unresolved_exceptions)``.

    Even with unresolved required fields, returns all successfully-
    resolved answers so the caller can build the review-queue payload.
    """
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


# ─── Private helpers ────────────────────────────────────────────────────


def _resolve_file(
    spec: FieldSpec, cover_letter_text: str | None, resume_path: str | None,
) -> ResolvedField:
    """Handle the three file-kind cases: resume, cover_letter, other."""
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


def _resolve_resume_text(spec: FieldSpec, track: str) -> ResolvedField:
    """Extract plaintext resume from the .tex source for the given track.

    Some tenants render BOTH the file-upload input AND a paste-text
    textarea and require the textarea to be non-empty. We derive the
    plain-text version from the SAME .tex source the PDF was compiled
    from — guaranteed identical content, no LLM round-trip.

    On failure (missing resumes submodule in tests, etc.), return an
    empty-value ResolvedField with ``source="machine_key"`` so the
    batch resolver doesn't try to fill it.
    """
    try:
        from autoapply.config import get_settings
        from autoapply.profile.tex_to_text import resume_plaintext_for_track

        settings = get_settings()
        text = resume_plaintext_for_track(str(settings.resumes_dir), track)
        return ResolvedField(spec.name, spec.label, text, "machine_key")
    except Exception as exc:
        log.debug(
            "resume_text .tex plaintext extraction failed for track=%s: %s",
            track, exc,
        )
        return ResolvedField(spec.name, spec.label, "", "machine_key")


def _try_per_field_llm(
    spec: FieldSpec, profile: Profile, track: str,
) -> str | None:
    """Ask the per-field Gemini fallback to pick from ``spec.options``.

    Returns None on error (keeps the caller in deterministic-failure
    mode) rather than bubbling the exception — the batched resolver
    downstream is a better home for LLM fallbacks, and any per-field
    LLM flakiness shouldn't crash the whole Phase-1 pass.
    """
    try:
        from autoapply.answers.llm_fallback import draft_field_answer

        return draft_field_answer(
            label=spec.label, spec=spec, profile=profile, track=track,
        )
    except Exception as exc:
        log.debug("llm_fallback error for label=%r: %s", spec.label, exc)
        return None
