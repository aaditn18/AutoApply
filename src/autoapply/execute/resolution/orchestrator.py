"""Phase-0 orchestrator — ``resolve_all_batched``.

The public two-phase resolver. Composes the smaller modules:

  Phase 1 — :mod:`.phase1`         — deterministic resolve_all.
  Phase 2 — :mod:`.batch_builder`  — pick the fields to send to the LLM.
  Phase 3 — :mod:`autoapply.answers.llm_batch.resolve_batch`
                                   — ONE Gemini call.
  Phase 4 — :mod:`.backfill`       — apply LLM answers onto Phase-1 state.

The function signature and return shape match the old monolithic
version in ``standard_fields.py`` so the shim + all callers keep
working unchanged.
"""

from __future__ import annotations

import logging
from typing import Any

from autoapply.answers import classifier as classifier_mod
from autoapply.answers.bank import AnswerBank
from autoapply.profile.schema import Profile
from autoapply.execute.standard_fields import (
    ClassifyFn,
    FieldSpec,
    ResolvedField,
    UnresolvedField,
)

from .backfill import apply_answers
from .batch_builder import build_batch
from .phase1 import resolve_all


log = logging.getLogger(__name__)


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

    Returns ``(resolved, unresolved, audit)`` where ``audit`` contains::

        {
          "batch_asked": [question_id, ...],   # what we sent to LLM
          "model_used": "gemini-2.5-flash-lite",
          "cascade_trace": [{...}, {...}],     # per-model attempt log
          "error": "" | "<final failure msg>",
          "answer_count": N,
        }

    The caller logs this and stores it in ``Application.artifacts`` so
    we can audit per-field resolution sources after the fact.
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

    # Phase 2 — decide which fields need the LLM.
    plan = build_batch(specs, resolved, unresolved)

    audit: dict[str, Any] = {
        "batch_asked": [q.id for q in plan.questions],
        "model_used": "",
        "cascade_trace": [],
        "error": "",
        "answer_count": 0,
    }

    # Phase 1 answered everything — skip the LLM.
    if not plan.questions:
        return resolved, unresolved, audit

    # Phase 3 — the one LLM call.
    from autoapply.answers.llm_batch import resolve_batch

    if answer_bank_yaml is None:
        answer_bank_yaml = _load_bank_yaml_text()

    result = resolve_batch(
        questions=plan.questions,
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
        # Batch failed wholesale — leave Phase-1 state intact. The
        # caller will route to review for any required unresolved.
        log.warning(
            "resolve_all_batched: batch resolution failed: %s", result.error,
        )
        return resolved, unresolved, audit

    # Phase 4 — backfill LLM answers onto the Phase-1 state.
    resolved, still_unresolved = apply_answers(
        specs=specs,
        resolved=resolved,
        unresolved=unresolved,
        answers=result.answers,
    )
    return resolved, still_unresolved, audit


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
