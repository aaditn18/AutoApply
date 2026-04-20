"""Phase-4 backfill — apply LLM answers back onto Phase-1 state.

After the batched LLM returns, we walk its answers and:

- Update Phase-1 ResolvedField values in place (clearing the
  ``requires_llm`` / ``requires_review`` flags that got us here).
- Promote any previously-unresolved questions to resolved by
  synthesizing a fresh ResolvedField from the LLM answer.
- Skip ``source="needs_review"`` answers — the LLM explicitly declined,
  so we keep the Phase-1 state intact (unresolved stays unresolved).

Multi-select values from the LLM come back as ``list[str]``; we join
them into the comma-separated form downstream code expects.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from autoapply.execute.standard_fields import (
    FieldSpec,
    ResolvedField,
    UnresolvedField,
)


if TYPE_CHECKING:
    from autoapply.answers.llm_batch import BatchAnswer


def apply_answers(
    *,
    specs: list[FieldSpec],
    resolved: list[ResolvedField],
    unresolved: list[UnresolvedField],
    answers: dict[str, "BatchAnswer"],
) -> tuple[list[ResolvedField], list[UnresolvedField]]:
    """Fold LLM answers into the Phase-1 resolution lists.

    Mutates the ``resolved`` list in place (for backfill of existing
    ResolvedField entries) and returns updated lists. ``unresolved`` is
    rewritten to exclude promoted entries.
    """
    spec_by_name = {s.name: s for s in specs}
    resolved_by_name = {r.name: r for r in resolved}
    still_unresolved = list(unresolved)

    for qid, answer in answers.items():
        sp = spec_by_name.get(qid)
        if sp is None:
            continue

        if answer.source == "needs_review":
            # LLM declined. Leave Phase-1 state as-is; unresolved stays unresolved.
            continue

        val = _serialize_answer_value(answer.value)

        if qid in resolved_by_name:
            r = resolved_by_name[qid]
            r.value = val
            r.source = f"llm_batch:{answer.source}"
            r.requires_llm = False
            r.requires_review = False
        else:
            # Promote from unresolved to resolved.
            resolved.append(ResolvedField(
                name=qid,
                label=sp.label or qid,
                value=val,
                source=f"llm_batch:{answer.source}",
            ))
            still_unresolved = [u for u in still_unresolved if u.name != qid]

    return resolved, still_unresolved


def _serialize_answer_value(value) -> str:
    """Convert an LLM-returned value (str / list / None) to the plain
    string form downstream code expects."""
    if isinstance(value, list):
        return ", ".join(value)
    if value is None:
        return ""
    return str(value)
