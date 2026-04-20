"""Batch LLM resolver — public facade.

Exposes the public dataclasses and :func:`resolve_batch`; the actual
pipeline is composed from three specialized modules:

  * :mod:`autoapply.adapters.gemini`        — SDK wrapper + model cascade.
  * :mod:`autoapply.answers.batch_prompt`   — prompt construction.
  * :mod:`autoapply.answers.batch_parse`    — response validation.

Why this module is a facade rather than the implementation:
  * **Separation of concerns** — each piece changes on a different
    cadence. The Gemini SDK deprecates models every 6 months; the
    prompt gets iterated on when essays land flat; the parser evolves
    when the model's JSON-format compliance regresses. Keeping them
    in separate files means a Gemini change is a one-file diff that
    doesn't also touch prompt engineering code.
  * **Backcompat** — tests (and ``conftest.py``) monkeypatch
    ``autoapply.answers.llm_batch._call_with_cascade``. That name is
    re-exported here so the hermetic-Gemini fixture keeps working
    unchanged.

Module layout
-------------
    BatchQuestion           — input record describing one form field
    BatchAnswer             — output record with value + source + confidence
    BatchResult             — full result: answers + cascade trace + errors
    MODEL_CASCADE           — ordered tuple of model IDs (re-export)
    _CASCADE_ERROR_MARKERS  — error-string markers (re-export)
    _is_cascade_error       — error classifier (re-export)
    _call_with_cascade      — internal callable tests monkeypatch
    _build_prompt           — internal prompt constructor (re-export)
    _parse_response         — internal response parser (re-export)
    resolve_batch           — public entry point
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

# Re-exported from adapters.gemini — the names are kept on this module
# for backcompat with tests and with call sites that imported them
# before the split.
from autoapply.adapters.gemini import (
    MODEL_CASCADE,
    _CASCADE_ERROR_MARKERS,
    _is_cascade_error,
)
from autoapply.adapters.gemini import call_with_cascade as _gemini_call


if TYPE_CHECKING:
    from autoapply.profile.schema import Profile

log = logging.getLogger(__name__)


# ─── Public data structures ─────────────────────────────────────────────


@dataclass
class BatchQuestion:
    """One form field to resolve via the LLM batch.

    Attributes:
      id:       stable key used to correlate the LLM answer back to the
                form field (typically the Playwright ``name``/``id`` attr).
      label:    the human-readable question text scraped from the DOM or
                API. Untrusted input — sanitized before embedding.
      kind:     ``"select"`` | ``"multi_select"`` | ``"text"`` | ``"textarea"``.
      required: whether the form marks the field as required. We only
                include required+unresolved fields in the batch — optional
                unresolved fields are left blank.
      options:  for select/multi_select, the list of option labels scraped
                from the DOM or API. Must be non-empty for select kinds.
    """

    id: str
    label: str
    kind: str
    required: bool = True
    options: list[str] = field(default_factory=list)


@dataclass
class BatchAnswer:
    """One resolved field returned by the LLM.

    ``value`` is the exact text to type / select. For ``select`` kinds it
    MUST be one of the option strings we passed in ``BatchQuestion.options``
    verbatim; we validate this post-parse and reject mismatches.

    ``confidence`` is the LLM's self-reported 0.0–1.0 score. We trust it
    only when ≥ 0.5; below that threshold the field goes to review.

    ``source`` is one of:
      ``"llm_reasoning"``     — LLM derived the answer from profile/bank
      ``"llm_generation"``    — LLM wrote free-text (essays, cover letters)
      ``"llm_option_match"``  — LLM matched a bank/profile value to a
                                specific dropdown option
      ``"needs_review"``      — LLM could not confidently answer
    """

    question_id: str
    value: str | None
    source: str
    confidence: float = 1.0
    reasoning: str = ""


@dataclass
class BatchResult:
    """Complete result of one LLM batch call.

    ``answers``: the resolved fields keyed by ``BatchQuestion.id``.
    ``model_used``: the first model in ``MODEL_CASCADE`` that returned
                    a usable response (useful for observability).
    ``cascade_trace``: per-attempt log — which model was tried and what
                       error (if any) caused the fallback. Serialized to
                       the DB audit log.
    ``error``: top-level failure message if ALL models were exhausted.
               When present, ``answers`` is empty.
    """

    answers: dict[str, BatchAnswer] = field(default_factory=dict)
    model_used: str = ""
    cascade_trace: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""


# ─── Public entry point ─────────────────────────────────────────────────


def resolve_batch(
    *,
    questions: list[BatchQuestion],
    profile: "Profile",
    answer_bank_yaml: str,
    track: str = "swe",
    company: str = "",
    job_title: str = "",
    job_description: str = "",
) -> BatchResult:
    """Batch-resolve form fields via a single LLM call with cascade fallback.

    Filters to required+nontrivial questions before calling — if no
    questions qualify, returns an empty result immediately (zero-cost).

    Args:
      questions: the full list of form fields. Non-required fields and
        fields whose ``kind`` is a trivial text atom (where the classifier
        would handle it better) should already be filtered out by the
        caller; this function only enforces the ``required`` filter.
      profile: parsed resume facts for the chosen track.
      answer_bank_yaml: the raw YAML contents of ``state/answer_bank.yml``.
        Passed as a string so the LLM can see both keys and values.
      track: resume track in use. Embedded in the prompt.
      company, job_title: optional metadata for essay tailoring.
      job_description: optional JD text for essay tailoring. Wrapped in
        ``<UNTRUSTED>`` in the prompt.

    Returns:
      :class:`BatchResult`. If ``error`` is set, ``answers`` is empty and
      the caller should route the application to review.
    """
    filtered = [q for q in questions if q.required]
    if not filtered:
        return BatchResult(
            model_used="",
            cascade_trace=[{"skipped": "no required unresolved questions"}],
        )

    from autoapply.config import get_settings

    settings = get_settings()
    api_key = getattr(settings, "GEMINI_API_KEY", "") or ""
    if not api_key:
        return BatchResult(error="GEMINI_API_KEY not set")

    from autoapply.answers.batch_prompt import build_prompt

    prompt = build_prompt(
        questions=filtered,
        profile=profile,
        answer_bank_yaml=answer_bank_yaml,
        track=track,
        company=company,
        job_title=job_title,
        job_description=job_description,
    )

    return _call_with_cascade(prompt, api_key, filtered)


# ─── Internal cascade caller ────────────────────────────────────────────
#
# Named ``_call_with_cascade`` (underscore prefix) for backcompat with
# test fixtures that monkeypatch this attribute on the module
# (``tests/conftest.py`` + ``tests/test_llm_batch.py``). The real
# implementation is in :mod:`autoapply.adapters.gemini`; this wrapper
# adapts the adapter's (result, model, error, trace) tuple into a
# ``BatchResult``.


def _call_with_cascade(
    prompt: str,
    api_key: str,
    questions: list[BatchQuestion],
) -> BatchResult:
    """Try each model in ``MODEL_CASCADE`` until one succeeds or all fail."""
    from autoapply.answers.batch_parse import parse_response

    def _parse(text: str) -> dict[str, BatchAnswer]:
        return parse_response(text, questions)

    answers, model_used, error, trace = _gemini_call(
        prompt=prompt,
        api_key=api_key,
        parse=_parse,
    )
    if error or not answers:
        return BatchResult(
            answers={},
            model_used=model_used,
            cascade_trace=trace,
            error=error or "empty answer set",
        )
    return BatchResult(
        answers=answers,
        model_used=model_used,
        cascade_trace=trace,
    )


# ─── Back-compat re-exports ─────────────────────────────────────────────
# Names some tests and external modules still import from this module.


def _build_prompt(*args: Any, **kwargs: Any) -> str:
    """Back-compat shim. New code should import from ``batch_prompt``."""
    from autoapply.answers.batch_prompt import build_prompt

    return build_prompt(*args, **kwargs)


def _parse_response(
    raw_text: str, questions: list[BatchQuestion]
) -> dict[str, BatchAnswer]:
    """Back-compat shim. New code should import from ``batch_parse``."""
    from autoapply.answers.batch_parse import parse_response

    return parse_response(raw_text, questions)


def _profile_as_json(profile: "Profile", track: str) -> str:
    """Back-compat shim. New code should import from ``batch_prompt``."""
    from autoapply.answers.batch_prompt import profile_as_json

    return profile_as_json(profile, track)


def _validate_select_value(value: Any, options: list[str]) -> str | None:
    """Back-compat shim."""
    from autoapply.answers.batch_parse import _validate_select_value as _f

    return _f(value, options)


def _validate_multi_select_value(
    value: Any, options: list[str],
) -> list[str] | None:
    """Back-compat shim."""
    from autoapply.answers.batch_parse import _validate_multi_select_value as _f

    return _f(value, options)


__all__ = [
    # Public dataclasses
    "BatchQuestion",
    "BatchAnswer",
    "BatchResult",
    # Public entry point
    "resolve_batch",
    # Adapter re-exports (module cascade / errors — see adapters.gemini)
    "MODEL_CASCADE",
    "_CASCADE_ERROR_MARKERS",
    "_is_cascade_error",
    # Back-compat internal handles
    "_call_with_cascade",
    "_build_prompt",
    "_parse_response",
    "_profile_as_json",
    "_validate_select_value",
    "_validate_multi_select_value",
]
