"""Build the review-queue flag payload from a resolved field list.

Extracted from :func:`autoapply.execute.base.Applicator.apply` so the
routing decision ("does this application need review?") is independent
of the submit/dry-run mechanics.

Two flag sources:

- :class:`UnresolvedField` exceptions raised during Phase-1 resolution.
  Every unresolved REQUIRED field must have a flag so the GH-Issues
  review UI can surface what the reviewer has to answer.
- :class:`ResolvedField` entries flagged ``requires_llm`` /
  ``requires_review``. These were routed to review because the batch
  LLM couldn't confidently resolve them OR because the classifier
  returned a policy-sensitive type.

Output shape matches the schema downstream consumers (:mod:`review.gh_issues`)
expect — not changed by this refactor.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from autoapply.execute.standard_fields import (
        FieldSpec,
        ResolvedField,
        UnresolvedField,
    )


def build_review_payload(
    specs: list["FieldSpec"],
    resolved: list["ResolvedField"],
    unresolved: list["UnresolvedField"],
) -> tuple[list[str], list[dict[str, Any]]]:
    """Return ``(review_reasons, review_flags)`` for the GH-Issues payload.

    ``review_reasons`` is a compact list of tags like
    ``"unresolved:first_name"`` / ``"llm:why_company"`` / ``"review:felony"``
    for the issue title.

    ``review_flags`` is the structured per-field payload the GH-Issues
    renderer uses to show the reviewer what the field looked like, which
    options were available, and what we attempted.
    """
    spec_by_name = {s.name: s for s in specs}
    reasons: list[str] = []
    flags: list[dict[str, Any]] = []

    for u in unresolved:
        reasons.append(f"unresolved:{u.name or u.label}")
        sp = spec_by_name.get(u.name)
        flags.append({
            "field_name": u.name,
            "field_label": u.label,
            "field_kind": sp.kind if sp else "",
            "required": sp.required if sp else False,
            "options": list(sp.options) if sp else [],
            "reason": "unresolved",
            "question_type": None,
            "attempted_value": "",
        })

    for r in resolved:
        if not (r.requires_llm or r.requires_review):
            continue
        tag = "llm" if r.requires_llm else "review"
        reasons.append(f"{tag}:{r.name}")
        sp = spec_by_name.get(r.name)
        # Normalize question_type to a string for JSON serialization.
        qt = (
            r.question_type.value
            if r.question_type and hasattr(r.question_type, "value")
            else (r.question_type if isinstance(r.question_type, str) else None)
        )
        flags.append({
            "field_name": r.name,
            "field_label": r.label,
            "field_kind": sp.kind if sp else "",
            "required": sp.required if sp else False,
            "options": list(sp.options) if sp else [],
            "reason": "requires_llm" if r.requires_llm else "requires_review",
            "question_type": qt,
            "attempted_value": r.value or "",
        })

    return reasons, flags
