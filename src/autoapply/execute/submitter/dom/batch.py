"""Stage-2 DOM-batch orchestrator — the only public entry point of :mod:`.dom`.

Composes the other submodules into the full pipeline:

    scrape options (per field, lazily)
    → pre-resolve each field via classifier + profile + bank
    → late-rescan (EEO fields that only render after others commit)
    → batch any remaining required fields into one Gemini call
    → fill each answered field
    → return per-field audit dict

Every field — whether pre-resolved or LLM-resolved — gets a per_field
entry in the audit so the caller can log exactly what was filled,
with what source and confidence. See the ``audit`` docstring below for
the schema.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from autoapply.answers.llm_batch import BatchQuestion, resolve_batch

from .fields import _DomField
from .fill import _fill_one
from .options import (
    _close_dropdown_state,
    _scrape_async_typeahead_options,
    _scrape_select_options,
)
from .resolve import _try_classifier_resolve
from .scrape import collect_empty_required_fields


if TYPE_CHECKING:
    from autoapply.profile.schema import Profile


log = logging.getLogger(__name__)


def batch_resolve_dom_fields(
    *,
    page: Any,
    profile: "Profile",
    answer_bank_yaml: str,
    track: str = "swe",
    company: str = "",
    job_title: str = "",
    already_filled_keys: set[str] | None = None,
) -> dict[str, Any]:
    """Scrape empty required fields, batch-LLM, fill.

    Returns an audit dict::

        {
          "scraped_count": N,
          "filled_count": M,
          "model_used": "gemini-2.5-flash-lite",
          "cascade_trace": [...],
          "error": "",
          "per_field": [{field_id, label, value, source, confidence}, ...],
        }

    Callers log this under the same "batch-llm" channel as Stage-1. The
    ``per_field`` list is the audit trail — which DOM-injected questions
    were resolved by the LLM and what answer they got.
    """
    already_filled_keys = already_filled_keys or set()

    audit: dict[str, Any] = {
        "scraped_count": 0,
        "filled_count": 0,
        "model_used": "",
        "cascade_trace": [],
        "error": "",
        "per_field": [],
    }

    fields = collect_empty_required_fields(page, already_filled_keys)
    audit["scraped_count"] = len(fields)

    if not fields:
        return audit

    log.info(
        "dom_batch: scraped %d empty required fields — %s",
        len(fields),
        [f"{f.element_id}({f.kind})" for f in fields][:8],
    )

    _populate_select_options(page, fields)

    # ── Pre-resolve pass ────────────────────────────────────────────────
    # For each scraped field, try the deterministic classifier + profile
    # + bank pipeline BEFORE sending to the LLM. Each pre-resolved field
    # is filled immediately and removed from the batch.
    preresolved_ids: set[str] = set()
    _run_preresolve(
        page, fields, profile=profile, track=track,
        audit=audit, preresolved_ids=preresolved_ids, late=False,
    )

    # ── Late rescan ────────────────────────────────────────────────────
    # Filling some fields (especially cascading EEO selects) may have
    # revealed new required fields that weren't visible / required at
    # the initial scrape. This catches the Axon ``Please identify your
    # race`` field, which is rendered only after Gender has a value.
    late_fields = _rescan_for_late_fields(
        page, already_seen_ids={f.element_id for f in fields},
        already_filled_keys=already_filled_keys,
    )
    if late_fields:
        _populate_select_options(page, late_fields)
        _run_preresolve(
            page, late_fields, profile=profile, track=track,
            audit=audit, preresolved_ids=preresolved_ids, late=True,
        )
        # Merge late fields into the set that gets sent to the LLM.
        fields = fields + late_fields

    # ── Only send remaining fields to the LLM ──────────────────────────
    remaining = [f for f in fields if f.element_id not in preresolved_ids]
    if not remaining:
        return audit

    batch_qs = [
        BatchQuestion(
            id=f.element_id,
            label=f.label,
            kind=f.kind,
            required=True,
            options=list(f.options),
        )
        for f in remaining
    ]

    # One LLM call.
    result = resolve_batch(
        questions=batch_qs,
        profile=profile,
        answer_bank_yaml=answer_bank_yaml,
        track=track,
        company=company,
        job_title=job_title,
    )

    audit["model_used"] = result.model_used
    audit["cascade_trace"] = result.cascade_trace
    audit["error"] = result.error

    if result.error or not result.answers:
        log.warning("dom_batch: batch resolution failed: %s", result.error)
        return audit

    _apply_llm_answers(page, fields, result.answers, audit)

    return audit


# ─── Internal helpers ──────────────────────────────────────────────────


def _populate_select_options(page: Any, fields: list[_DomField]) -> None:
    """Scrape options for each ``kind="select"`` field.

    Tries the sync ``_scrape_select_options`` first; if it returns
    empty (async-typeahead), classifies the label and picks a seed
    string to trigger the XHR load. If still empty, downgrades the
    field to ``kind="text"`` so the LLM writes a freeform answer that
    ``fill_combobox`` types and picks from the async-loaded menu.
    """
    from autoapply.answers.classifier import classify as _classify_for_seed
    from autoapply.answers.types import QuestionType as _QT

    for f in fields:
        if f.kind != "select":
            continue
        f.options = _scrape_select_options(page, f.locator)
        if f.options:
            continue

        # Empty-on-open: probably an async typeahead. Try a seed.
        try:
            classified = _classify_for_seed(f.label)
        except Exception:
            classified = None
        seed = ""
        if classified is not None:
            if classified.type is _QT.SCHOOL:
                # 'university' loads most US schools; 'maryland' narrows faster.
                seed = "university of mary"
            elif classified.type is _QT.MAJOR:
                seed = "computer"
            elif classified.type is _QT.DEGREE:
                seed = "bachelor"
        if seed:
            f.options = _scrape_async_typeahead_options(page, f.locator, seed)
            if f.options:
                log.info(
                    "dom_batch: async-typeahead seed=%r loaded %d options for %s",
                    seed, len(f.options), f.element_id,
                )
                continue

        # Still nothing — downgrade to text so the LLM writes a freetext
        # answer the combobox filler can type + async-filter.
        log.debug(
            "dom_batch: no options scraped for %s — downgrading to text",
            f.element_id,
        )
        f.kind = "text"


def _run_preresolve(
    page: Any,
    fields: list[_DomField],
    *,
    profile: "Profile",
    track: str,
    audit: dict[str, Any],
    preresolved_ids: set[str],
    late: bool,
) -> None:
    """Run the classifier+profile pre-resolve over ``fields``.

    Each field that resolves to a non-empty value is filled immediately;
    ``preresolved_ids`` records which fields shouldn't go to the LLM.
    ``late=True`` tags the audit entries so the log line makes clear
    this was from the post-initial-fill rescan pass.
    """
    src_tag = "classifier+profile (late)" if late else "classifier+profile"
    reasoning = (
        "resolved from profile pre-LLM, late scan"
        if late else "resolved from profile pre-LLM"
    )
    for f in fields:
        synth_value = _try_classifier_resolve(f, profile=profile, track=track)
        if not late:
            log.info(
                "pre-resolve attempt: id=%s label=%r synth=%r kind=%s",
                f.element_id, (f.label or "")[:60],
                (synth_value or "")[:60], f.kind,
            )
        if not synth_value:
            continue

        ok = _fill_one(page, f, synth_value)
        if not late:
            log.info(
                "pre-resolve fill result: id=%s filled=%s", f.element_id, ok,
            )
        audit["per_field"].append({
            "field_id": f.element_id,
            "label": f.label,
            "value": synth_value[:120],
            "source": src_tag,
            "confidence": 1.0,
            "reasoning": reasoning,
            "filled": ok,
        })
        if ok:
            audit["filled_count"] += 1
            preresolved_ids.add(f.element_id)
            prefix = "late pre-resolved" if late else "pre-resolved"
            log.info(
                "dom_batch: %s %s (%r) ← %r [%s]",
                prefix, f.element_id, (f.label or "")[:50],
                synth_value[:60], src_tag,
            )
            _close_dropdown_state(page)


def _rescan_for_late_fields(
    page: Any, *, already_seen_ids: set[str], already_filled_keys: set[str],
) -> list[_DomField]:
    """Re-scrape the DOM for fields that weren't visible on the first pass.

    EEO cascades are the primary case: Axon's "Please identify your
    race" field only renders after Gender has a value.
    """
    try:
        new_fields = collect_empty_required_fields(page, already_filled_keys)
    except Exception as exc:
        log.debug("dom_batch: rescan failed: %s", exc)
        return []
    late = [f for f in new_fields if f.element_id not in already_seen_ids]
    if late:
        log.info(
            "dom_batch: rescan found %d new required field(s): %s",
            len(late),
            [f.element_id for f in late][:10],
        )
    return late


def _apply_llm_answers(
    page: Any,
    fields: list[_DomField],
    answers: dict[str, Any],
    audit: dict[str, Any],
) -> None:
    """Fill each LLM-answered field; record the outcome in the audit.

    Blurs focus between fills — see :func:`._close_dropdown_state` for
    why a direct blur beats Tab / Escape / mouse-click-elsewhere.
    """
    field_by_id = {f.element_id: f for f in fields}
    for qid, answer in answers.items():
        f = field_by_id.get(qid)
        if f is None:
            continue
        if answer.source == "needs_review" or answer.value is None:
            audit["per_field"].append({
                "field_id": qid,
                "label": f.label,
                "value": None,
                "source": answer.source,
                "confidence": answer.confidence,
                "reasoning": answer.reasoning,
                "filled": False,
            })
            continue

        # Multi-select → join to comma-separated; caller's fill logic
        # handles the breakdown.
        if isinstance(answer.value, list):
            val = ", ".join(str(v) for v in answer.value)
        else:
            val = str(answer.value)

        filled_ok = _fill_one(page, f, val)
        audit["per_field"].append({
            "field_id": qid,
            "label": f.label,
            "value": val[:120],
            "source": answer.source,
            "confidence": answer.confidence,
            "reasoning": answer.reasoning,
            "filled": filled_ok,
        })
        if filled_ok:
            audit["filled_count"] += 1
            log.info(
                "dom_batch: filled %s (%r) ← %r [%s]",
                qid, (f.label or "")[:50], val[:60], answer.source,
            )
        # Always commit + blur between fills so the React-Select
        # reconciler finishes writing the selection before the next
        # field's mousedown fires. Without this, observations on Axon
        # EEO showed Gender committing but the subsequent Hispanic
        # fill's events arrived while Gender was still propagating,
        # and the net state had Hispanic committed but Gender's
        # commit lost.
        _close_dropdown_state(page)
