"""Stage-2 DOM resolver — scrape SPA-injected fields, batch-LLM, fill.

This package was extracted from the 1200-LOC ``dom_batch.py`` module.
Each submodule owns ONE concern; :mod:`batch` is the only public entry
point (:func:`batch.batch_resolve_dom_fields`).

Layout
------

    fields       — ``_DomField`` dataclass; shared across all submodules.
    preferences  — Ordered regex preferences for education dropdowns
                   (loaded from ``state/rules/education_preferences.yml``)
                   plus the US-states frozenset (from ``geography.yml``).
                   Pure data + pure functions; no Playwright.
    options      — React-Select detection + option harvesting for both
                   sync (click-to-open) and async-typeahead (type-to-load)
                   dropdowns. Playwright-bound.
    scrape       — :func:`collect_empty_required_fields` — DOM walk for
                   required empty inputs. Playwright-bound.
    resolve      — :func:`_try_classifier_resolve` — pre-resolve via the
                   classifier + profile + bank BEFORE calling the LLM.
    fill         — :func:`_fill_one` — per-field fill dispatch
                   (checkbox / native-select / React-Select / text).
    batch        — :func:`batch_resolve_dom_fields` — the orchestrator
                   that wires everything together: scrape → options →
                   pre-resolve → late-rescan → LLM → fill → audit.

External callers should ``from .dom.batch import batch_resolve_dom_fields``.
The ``execute/submitter/dom_batch.py`` module remains as a backwards-
compatible re-export shim.
"""

from .batch import batch_resolve_dom_fields
from .fields import _DomField
from .preferences import (
    _DEGREE_OPTION_PREFERENCES,
    _DISCIPLINE_OPTION_PREFERENCES,
    _SCHOOL_OPTION_PREFERENCES,
    _US_STATE_LABELS,
    _education_patterns_for_label,
    _match_preferred_option,
)
from .scrape import collect_empty_required_fields

__all__ = [
    "batch_resolve_dom_fields",
    "collect_empty_required_fields",
    "_DomField",
    "_DEGREE_OPTION_PREFERENCES",
    "_DISCIPLINE_OPTION_PREFERENCES",
    "_SCHOOL_OPTION_PREFERENCES",
    "_US_STATE_LABELS",
    "_education_patterns_for_label",
    "_match_preferred_option",
]
