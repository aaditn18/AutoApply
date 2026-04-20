"""Per-field audit logging for the resolver pipeline.

Splits the 75-LOC ``_log_resolution_audit`` out of
:class:`autoapply.execute.base.Applicator` so the formatter can be
unit-tested independently and reused outside the Applicator class
(e.g., a CLI command that re-runs the resolver for diagnostic purposes
without actually submitting).

Two concerns live here:

- :func:`bucket_source` — turn a ResolvedField's ``source`` string into
  a short human-readable bucket name. Pure function, easy to test.
- :func:`log_resolution_audit` — emit the grouped audit lines at INFO
  level. Side-effectful (calls ``log.info``) but deterministic in
  what it emits for a given ``(resolved, unresolved)`` pair.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from autoapply.execute.standard_fields import ResolvedField, UnresolvedField


log = logging.getLogger(__name__)


# Stable display order for the audit log. Buckets not in this tuple are
# appended in alphabetical order after the known ones.
_BUCKET_ORDER: tuple[str, ...] = (
    "profile",
    "bank",
    "classifier",
    "llm_batch",
    "llm_single",
    "review",
    "none",
)


def bucket_source(source: str) -> str:
    """Turn a ``ResolvedField.source`` string into a short bucket name.

    Sources we surface:
      * ``profile``    — ``machine_key`` or ``profile``
                         (PROFILE_SOURCED from resume).
      * ``bank``       — anything starting with ``bank`` (answer_bank.yml hit).
      * ``classifier`` — generic ``classifier+bank`` hit.
      * ``llm_batch``  — answered by the batched Gemini call (any sub-source).
      * ``llm_single`` — legacy per-field LLM (``llm_answer``).
      * ``review``     — flagged for human review
                         (``review_required`` / ``llm_required``).
      * ``none``       — optional, left blank deliberately.
      * anything else is passed through unchanged.
    """
    src = source or "none"
    if src == "machine_key":
        return "profile"
    if src == "profile":
        return "profile"
    if src.startswith("bank"):
        return "bank"
    if src == "classifier+bank":
        return "classifier"
    if src.startswith("llm_batch"):
        return "llm_batch"
    if src == "llm_answer":
        return "llm_single"
    if src in ("review_required", "llm_required"):
        return "review"
    return src or "none"


def log_resolution_audit(
    resolved: list["ResolvedField"],
    unresolved: list["UnresolvedField"],
) -> None:
    """Emit a human-readable per-field audit log at INFO level.

    Output shape::

        === field audit: N answered, M unresolved ===
          [profile]      first_name           = 'Aadit'
          [profile]      email                = 'aaditnilay18@gmail.com'
          [classifier]   current_city         = 'College Park'
          [llm_batch]    question_5783087009  = 'LinkedIn'
          [review     ]  question_xxx         = (unresolved: Gender*)
    """
    buckets: dict[str, list[tuple[str, str]]] = {}
    for r in resolved:
        bucket = bucket_source(r.source)
        # Hide redundant noise (empty-value "none" entries from optional
        # fields the pipeline intentionally left blank).
        if bucket == "none" and not r.value:
            continue
        display_val = (r.value or "").replace("\n", " ")[:72]
        buckets.setdefault(bucket, []).append((r.name, display_val))

    total_answered = sum(len(v) for v in buckets.values())
    log.info(
        "=== field audit: %d answered, %d unresolved ===",
        total_answered,
        len(unresolved),
    )

    # Stable display order; unknown buckets appended alphabetically.
    ordered = list(_BUCKET_ORDER) + sorted(
        k for k in buckets if k not in _BUCKET_ORDER
    )
    for bucket in ordered:
        if bucket not in buckets:
            continue
        for name, val in buckets[bucket]:
            log.info(
                "  [%-11s] %-32s = %r", bucket, name[:32], val,
            )
    for u in unresolved:
        log.info(
            "  [review     ] %-32s = (unresolved: %s)",
            u.name[:32], (u.label or "").replace("\n", " ")[:60],
        )
