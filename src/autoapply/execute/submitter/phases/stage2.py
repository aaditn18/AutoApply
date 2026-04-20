"""Stage-2 DOM batch phase.

The new ``job-boards.greenhouse.io`` React SPA injects tenant-specific
required fields that are NOT in Greenhouse's API ``/questions``
response (School on jjsnackfoods, Gender on Axon/Smartsheet, Location
(City) on Fanatics, ...). Stage-1 already filled everything the API
exposed; Stage-2 scrapes the post-upload DOM for remaining empty
required fields, opens their dropdowns to capture options, and
resolves the whole batch in ONE Gemini call.

This phase is a thin wrapper around
:func:`autoapply.execute.submitter.dom_batch.batch_resolve_dom_fields`
that adds:

- Defensive try/except (a dom_batch crash should never kill the
  submission — worst case we lose Stage-2's contribution and rely on
  ``label_fallback`` + the submit attempt).
- Standardized audit logging: one summary line plus a per-field line
  that shows exactly what the LLM picked, with confidence score and
  whether the physical fill landed.
"""

from __future__ import annotations

import logging
from typing import Any

from ..dom_batch import batch_resolve_dom_fields


log = logging.getLogger(__name__)


def run_stage2_batch(
    page: Any,
    *,
    llm_context: dict[str, Any] | None,
    already_filled_keys: set[str],
) -> dict[str, Any]:
    """Resolve SPA-injected fields via one LLM call; log the audit.

    Returns the audit dict (empty if ``llm_context`` is None or the
    resolver raised). The audit is the same shape
    :func:`batch_resolve_dom_fields` returns — see that function's
    docstring for the schema.
    """
    if not llm_context:
        return {}

    try:
        audit = batch_resolve_dom_fields(
            page=page,
            profile=llm_context.get("profile"),
            answer_bank_yaml=llm_context.get("answer_bank_yaml", ""),
            track=llm_context.get("track", "swe"),
            company=llm_context.get("company", ""),
            job_title=llm_context.get("job_title", ""),
            already_filled_keys=already_filled_keys,
        )
    except Exception as exc:
        log.warning("dom_batch stage-2 raised: %s", exc)
        return {}

    _log_audit(audit)
    return audit


def _log_audit(audit: dict[str, Any]) -> None:
    """Emit the summary + per-field audit lines at INFO level."""
    if not audit.get("scraped_count"):
        return

    log.info(
        "dom-batch: scraped=%d filled=%d model=%s error=%s",
        audit.get("scraped_count", 0),
        audit.get("filled_count", 0),
        audit.get("model_used") or "<none>",
        audit.get("error") or "—",
    )
    # Per-field trace — critical for diagnosing "LLM answered but form
    # still rejected" cases where the physical fill failed after the
    # batch succeeded.
    for entry in audit.get("per_field", []):
        mark = "✓" if entry.get("filled") else "✗"
        log.info(
            "  %s [%s] %s = %r  (conf=%.2f, %s)",
            mark,
            entry.get("source", "?"),
            (entry.get("label") or entry.get("field_id", ""))[:50],
            str(entry.get("value") or "")[:60],
            entry.get("confidence", 0.0),
            entry.get("reasoning", "")[:60],
        )
