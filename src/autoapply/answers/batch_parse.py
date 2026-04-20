"""Response parsing + validation for the batch resolver.

Split from ``llm_batch.py`` — this module owns the output-format
contract: how to read the LLM's JSON response, how to validate that
selects are exact option matches, how to gate on confidence.

Safety rules, in order:

  1. Strip markdown fences (```json ... ```) if the model wrapped the
     JSON despite our structured-output directive.
  2. Parse as JSON; reject on failure (return empty dict).
  3. For each declared question id, look up its answer; missing →
     synthesized as ``needs_review``.
  4. ``type=select``: the returned ``value`` must be an exact match
     (case-insensitive fallback) for one of the question's options.
     Otherwise → ``needs_review``.
  5. ``type=multi_select``: value must be a JSON array; each element
     validated against options. Empty list → ``needs_review``.
  6. Confidence < 0.5 → forced to ``needs_review`` (caller routes to
     the human review queue rather than shipping a low-confidence
     answer).
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from autoapply.answers.llm_batch import BatchAnswer, BatchQuestion


log = logging.getLogger(__name__)


# Confidence threshold below which we force ``needs_review``.
_CONFIDENCE_THRESHOLD = 0.5


def parse_response(
    raw_text: str, questions: list["BatchQuestion"]
) -> dict[str, "BatchAnswer"]:
    """Validate + normalize the LLM's JSON output.

    Returns ``{question_id: BatchAnswer}``. Every question in
    ``questions`` is guaranteed a key — missing ones synthesize to
    ``needs_review``.
    """
    # Local import to avoid a circular import at module load
    # (llm_batch → batch_parse → llm_batch).
    from autoapply.answers.llm_batch import BatchAnswer

    raw = raw_text.strip()
    if raw.startswith("```"):
        # Tolerate ```json ... ``` markdown fences.
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```\s*$", "", raw)

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.warning("llm_batch: response JSON parse failed: %s", exc)
        return {}

    items = payload.get("answers") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        log.warning(
            "llm_batch: response missing 'answers' list; got %r",
            type(payload),
        )
        return {}

    q_by_id: dict[str, BatchQuestion] = {q.id: q for q in questions}

    out: dict[str, BatchAnswer] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("id") or "")
        if not qid or qid not in q_by_id:
            continue
        q = q_by_id[qid]
        value = item.get("value")
        source = str(item.get("source") or "needs_review")
        try:
            confidence = float(item.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        reasoning = str(item.get("reasoning") or "")[:200]

        # Normalize value for each kind.
        if q.kind == "select":
            value = _validate_select_value(value, q.options)
            if value is None:
                source = "needs_review"
        elif q.kind == "multi_select":
            value = _validate_multi_select_value(value, q.options)
            if value is None or not value:
                source = "needs_review"
        elif value is not None:
            value = str(value)

        # Confidence gate.
        if confidence < _CONFIDENCE_THRESHOLD and source != "needs_review":
            source = "needs_review"

        out[qid] = BatchAnswer(
            question_id=qid,
            value=value if source != "needs_review" else None,
            source=source,
            confidence=confidence,
            reasoning=reasoning,
        )

    # Fill in any questions the model omitted.
    for q in questions:
        if q.id not in out:
            out[q.id] = BatchAnswer(
                question_id=q.id,
                value=None,
                source="needs_review",
                confidence=0.0,
                reasoning="LLM omitted this question",
            )
    return out


def _validate_select_value(value: Any, options: list[str]) -> str | None:
    """Ensure a select value exactly matches one of the options.

    Tolerates case-only differences by case-insensitive match; returns
    the canonical (original-case) option. Returns ``None`` if no match.
    Substring matches do NOT count — React-Select rejects substrings
    during post-submit validation.
    """
    if not isinstance(value, str):
        return None
    v = value.strip()
    if not v:
        return None
    for opt in options:
        if opt == v:
            return opt
    for opt in options:
        if opt.lower() == v.lower():
            return opt
    return None


def _validate_multi_select_value(
    value: Any, options: list[str],
) -> list[str] | None:
    """Ensure a multi_select value is a list of valid option strings."""
    if not isinstance(value, list):
        return None
    out: list[str] = []
    for item in value:
        v = _validate_select_value(item, options)
        if v is not None:
            out.append(v)
    return out if out else None
