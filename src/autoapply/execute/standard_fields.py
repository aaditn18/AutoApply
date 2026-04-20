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
from autoapply.rules import load_rules


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


# Machine-key → profile attribute rules loaded from
# state/rules/machine_keys.yml. Compiled once at import (case-insensitive).
# See the rule file's header for ordering constraints.
_MACHINE_KEY_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(_row["pattern"], re.I), _row["attr"])
    for _row in load_rules("machine_keys")["rules"]
]


# ITAR / EAR export-control dropdown markers loaded from
# state/rules/export_control.yml. See the rule file for semantics.
_EXPORT_CONTROL_RULES = load_rules("export_control")
_US_PERSON_MARKERS: tuple[str, ...] = tuple(_EXPORT_CONTROL_RULES["us_person_markers"])
_OTHER_MARKERS: tuple[str, ...] = tuple(_EXPORT_CONTROL_RULES["other_markers"])


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
    if attr == "today_date":
        # Lever's EEO signature-date field. Common US format is MM/DD/YYYY.
        from datetime import date as _date
        return _date.today().strftime("%m/%d/%Y")
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
    # Text-area variant of resume upload (e.g. Greenhouse ``resume_text``).
    # Some tenants render BOTH the file-upload input AND a paste-text
    # textarea and require the textarea to be non-empty. We derive the
    # plain-text version from the SAME .tex source the PDF was
    # compiled from — guaranteed identical content, no LLM round-trip.
    # When the .tex isn't available (edge case in tests), fall back to
    # empty string + machine_key source so the batch resolver skips.
    if attr == "resume" and spec.kind in ("text", "textarea"):
        try:
            from autoapply.config import get_settings
            from autoapply.profile.tex_to_text import resume_plaintext_for_track

            settings = get_settings()
            text = resume_plaintext_for_track(
                str(settings.resumes_dir), track,
            )
            return ResolvedField(spec.name, spec.label, text, "machine_key")
        except Exception as exc:
            log.debug(
                "resume_text .tex plaintext extraction failed for track=%s: %s",
                track, exc,
            )
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
    # ONLY fires for ``select`` / ``multi_select`` kinds now — we need a
    # best-guess pick from the API's option list at resolver time so the
    # form's dropdowns have a concrete value. For free-response ``text``
    # / ``textarea`` kinds, we DEFER to the batched LLM resolver
    # (:mod:`autoapply.answers.llm_batch`) which gets the full job
    # description as context and can tailor essay answers accordingly.
    # Using the per-field ``draft_field_answer`` here would generate
    # a generic answer WITHOUT the JD context.
    if spec.kind in ("select", "multi_select"):
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
    # Markers are module-level constants loaded from state/rules/export_control.yml.
    has_us_options = any(
        any(m in opt.lower() for m in _US_PERSON_MARKERS)
        for opt in spec.options
    )
    if has_us_options:
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


# -- Batched-LLM resolver ----------------------------------------------------


def resolve_all_batched(
    specs: list[FieldSpec],
    *,
    profile: Profile,
    bank: AnswerBank,
    track: str,
    classify_fn: ClassifyFn = classifier_mod.classify,
    cover_letter_text: str | None = None,
    resume_path: str | None = None,
    company: str = "",
    job_title: str = "",
    job_description: str = "",
    answer_bank_yaml: str | None = None,
) -> tuple[list[ResolvedField], list[UnresolvedField], dict[str, Any]]:
    """Two-phase resolver: deterministic first, then ONE batched LLM call.

    Phase 1 — :func:`resolve_all` runs the existing classifier + bank +
    profile pipeline. Trivial fields (first_name, email, phone, etc.)
    resolve for free without touching the LLM.

    Phase 2 — identify **required** fields that Phase 1 couldn't resolve
    confidently AND would therefore block submission:
      * ``kind`` in ``select``/``multi_select`` AND required AND the Phase-1
        value isn't an exact option match → dropdown needs LLM to pick.
      * ``kind`` in ``text``/``textarea`` AND required AND Phase-1 flagged
        ``requires_llm`` / ``requires_review`` (unknown question type, or
        a ``LLM_REQUIRED`` type like ``why_company``) → needs LLM text.
      * Unresolved exceptions from Phase 1 — always batched if required.

    NON-required fields that Phase 1 couldn't answer are **left blank** by
    design — per the user's "leave blank" policy, we don't waste LLM
    tokens on optional questions. They were producing noise
    ("Decline to self-identify" for an optional pronouns field) and could
    route applications to review for no real reason.

    Phase 3 — call :func:`autoapply.answers.llm_batch.resolve_batch` with
    all collected questions in a single request. On success, backfill each
    Phase-1 ResolvedField's value and mark source=``llm_batch``.

    Returns ``(resolved, unresolved, audit)`` where ``audit`` contains::

        {
          "batch_asked": [question_id, ...],   # what we sent to LLM
          "model_used": "gemini-2.5-flash-lite",
          "cascade_trace": [{...}, {...}],     # per-model attempt log
          "error": "" | "<final failure msg>",
          "answer_count": N,
        }

    The caller logs this and stores it in ``Application.artifacts`` so we
    can audit per-field resolution sources after the fact.
    """
    # Phase 1 — deterministic.
    resolved, unresolved = resolve_all(
        specs,
        profile=profile,
        bank=bank,
        track=track,
        classify_fn=classify_fn,
        cover_letter_text=cover_letter_text,
        resume_path=resume_path,
    )

    # Phase 2 — build the batch.
    from autoapply.answers.llm_batch import BatchQuestion, resolve_batch

    spec_by_name = {s.name: s for s in specs}
    batch_qs: list[BatchQuestion] = []
    # Remember which ResolvedField we'll backfill for each batch id.
    backfill_map: dict[str, ResolvedField] = {}

    # A) From Phase-1 resolved list — the ones that were unconfident.
    for r in resolved:
        sp = spec_by_name.get(r.name)
        if sp is None or not sp.required:
            continue
        # Machine-key resolved fields (first_name, email, resume,
        # cover_letter, resume_text, ...) are intentionally set by
        # the applicator — including deliberately empty ones like
        # resume_text (the PDF upload carries the resume, so the
        # text field stays blank). NEVER batch these to the LLM, or
        # it'll helpfully paste the whole resume into a text field
        # the form actually ignores.
        if r.source == "machine_key":
            continue
        needs_batch = False

        if sp.kind in ("select", "multi_select"):
            # Required select with no exact option match? → batch it.
            # (The existing _snap_to_option tries hard but falls back to
            # the original value when nothing matches — that original
            # value won't work if the dropdown rejects it.)
            if not _value_matches_option(r.value, sp.options):
                needs_batch = True
        elif sp.kind in ("text", "textarea"):
            # Free-response that Phase 1 flagged as needing LLM or review.
            if r.requires_llm or r.requires_review:
                needs_batch = True
            elif not r.value:
                # Required text with no value — treat as needing LLM.
                needs_batch = True

        if needs_batch:
            batch_qs.append(BatchQuestion(
                id=r.name,
                label=r.label or sp.label or r.name,
                kind=sp.kind,
                required=True,
                options=list(sp.options),
            ))
            backfill_map[r.name] = r

    # B) From Phase-1 unresolved — always required by contract of UnresolvedField.
    for u in unresolved:
        sp = spec_by_name.get(u.name)
        if sp is None or not sp.required:
            continue
        batch_qs.append(BatchQuestion(
            id=u.name,
            label=u.label or sp.label or u.name,
            kind=sp.kind,
            required=True,
            options=list(sp.options),
        ))
        # No pre-existing ResolvedField — we'll synthesize one after the batch.
        backfill_map[u.name] = None  # type: ignore[assignment]

    audit: dict[str, Any] = {
        "batch_asked": [q.id for q in batch_qs],
        "model_used": "",
        "cascade_trace": [],
        "error": "",
        "answer_count": 0,
    }

    # No batch needed — Phase 1 answered everything.
    if not batch_qs:
        return resolved, unresolved, audit

    # Phase 3 — the one LLM call.
    if answer_bank_yaml is None:
        answer_bank_yaml = _load_bank_yaml_text()

    result = resolve_batch(
        questions=batch_qs,
        profile=profile,
        answer_bank_yaml=answer_bank_yaml,
        track=track,
        company=company,
        job_title=job_title,
        job_description=job_description,
    )

    audit["model_used"] = result.model_used
    audit["cascade_trace"] = result.cascade_trace
    audit["error"] = result.error
    audit["answer_count"] = len(result.answers)

    if result.error or not result.answers:
        # Batch failed wholesale — leave Phase-1 state intact. The caller
        # will route to review for any required unresolved.
        log.warning(
            "resolve_all_batched: batch resolution failed: %s", result.error,
        )
        return resolved, unresolved, audit

    # Phase 4 — backfill.
    resolved_by_name = {r.name: r for r in resolved}
    still_unresolved = [u for u in unresolved]  # we'll rewrite this list
    for qid, answer in result.answers.items():
        sp = spec_by_name.get(qid)
        if sp is None:
            continue

        # Multi-select values come back as list[str]; join into the
        # comma-separated form downstream code expects.
        val: str
        if isinstance(answer.value, list):
            val = ", ".join(answer.value)
        elif answer.value is None:
            val = ""
        else:
            val = str(answer.value)

        if answer.source == "needs_review":
            # LLM declined. Leave Phase-1 state as-is for this field.
            # If it was unresolved (required), it stays unresolved.
            continue

        if qid in resolved_by_name:
            r = resolved_by_name[qid]
            r.value = val
            r.source = f"llm_batch:{answer.source}"
            # Clear the LLM/review flags now that the batch resolved it.
            r.requires_llm = False
            r.requires_review = False
        else:
            # Was in `unresolved`; promote to resolved.
            resolved.append(ResolvedField(
                name=qid,
                label=sp.label or qid,
                value=val,
                source=f"llm_batch:{answer.source}",
            ))
            # Remove from unresolved.
            still_unresolved = [u for u in still_unresolved if u.name != qid]

    return resolved, still_unresolved, audit


def _value_matches_option(value: str, options: list[str]) -> bool:
    """Case-insensitive exact-match check for select values.

    Returns True only when the value exactly equals (case-insensitively)
    one of the options. Substring matches don't count — React-Select
    will reject a substring during post-submit validation, so we want
    the LLM to pick a verbatim option instead of trusting a loose match.
    """
    if not value or not options:
        return False
    v = value.strip().lower()
    return any(v == o.strip().lower() for o in options)


def _load_bank_yaml_text() -> str:
    """Load the raw YAML content of state/answer_bank.yml for the prompt.

    Returned as a string rather than a parsed dict so the LLM can see
    both keys and values — mimics how a human reading the file would.
    """
    try:
        from autoapply.config import get_settings

        settings = get_settings()
        return settings.answer_bank_path.read_text(encoding="utf-8")
    except Exception as exc:
        log.debug("_load_bank_yaml_text: %s", exc)
        return ""
