"""Decide which fields go into the Phase-2 batched LLM call.

After Phase 1, we have ``(resolved, unresolved)`` lists plus the
original ``FieldSpec`` records. This module decides which of those
need the batched LLM:

  * ``select`` / ``multi_select`` required — value doesn't exactly
    match an option string. (Substring matches don't count — React-
    Select rejects a substring during post-submit validation.)
  * ``text`` / ``textarea`` required — Phase 1 flagged the field as
    ``requires_llm`` / ``requires_review`` (e.g. WHY_COMPANY essay),
    OR the value is empty.
  * Phase-1 unresolved — always batched when the spec is required.

Non-required fields that Phase 1 couldn't answer are **left blank** by
design — per the "leave blank" policy, we don't burn LLM tokens on
optional questions.

Machine-key-resolved fields are NEVER batched, even when the value is
deliberately empty (e.g., ``resume_text`` when the PDF upload already
carries the resume). Batching those lets the LLM paste the whole
resume into a field the form ignores.
"""

from __future__ import annotations

from autoapply.answers.llm_batch import BatchQuestion
from autoapply.execute.standard_fields import (
    FieldSpec,
    ResolvedField,
    UnresolvedField,
)


class _Plan:
    """Output of :func:`build_batch` — what to send to the LLM and how
    to fold the answers back into state.

    Attributes:
      questions: list[BatchQuestion] to send.
      backfill_map: {qid → existing ResolvedField} for Phase-1 answers
        we're replacing. ``qid`` NOT in this map means the field was
        unresolved and will be synthesized from the answer.
    """

    __slots__ = ("questions", "backfill_map")

    def __init__(self) -> None:
        self.questions: list[BatchQuestion] = []
        self.backfill_map: dict[str, ResolvedField] = {}


def build_batch(
    specs: list[FieldSpec],
    resolved: list[ResolvedField],
    unresolved: list[UnresolvedField],
) -> _Plan:
    """Build the batch from Phase-1 state.

    Returns a :class:`_Plan` whose ``questions`` list is empty when
    Phase 1 answered everything (caller skips the LLM call entirely).
    """
    spec_by_name = {s.name: s for s in specs}
    plan = _Plan()

    # A) Phase-1 resolved that weren't confident.
    for r in resolved:
        sp = spec_by_name.get(r.name)
        if sp is None or not sp.required:
            continue
        # Machine-key-sourced fields (first_name, email, resume_text, ...)
        # are set by the applicator; never send them to the LLM.
        if r.source == "machine_key":
            continue

        needs_batch = False
        if sp.kind in ("select", "multi_select"):
            if not _value_matches_option(r.value, sp.options):
                needs_batch = True
        elif sp.kind in ("text", "textarea"):
            if r.requires_llm or r.requires_review:
                needs_batch = True
            elif not r.value:
                needs_batch = True

        if needs_batch:
            plan.questions.append(BatchQuestion(
                id=r.name,
                label=r.label or sp.label or r.name,
                kind=sp.kind,
                required=True,
                options=list(sp.options),
            ))
            plan.backfill_map[r.name] = r

    # B) Phase-1 unresolved — always required by contract.
    for u in unresolved:
        sp = spec_by_name.get(u.name)
        if sp is None or not sp.required:
            continue
        plan.questions.append(BatchQuestion(
            id=u.name,
            label=u.label or sp.label or u.name,
            kind=sp.kind,
            required=True,
            options=list(sp.options),
        ))
        # No pre-existing ResolvedField — we'll synthesize one in backfill.

    return plan


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
