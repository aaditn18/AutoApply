"""Backwards-compatible re-export shim.

The Stage-2 DOM pipeline used to live here as one 1200-LOC file. The
code now lives in the :mod:`.dom` subpackage, one module per concern
(``scrape``, ``options``, ``preferences``, ``resolve``, ``fill``,
``batch``). This module re-exports the public + test-accessed names so
existing imports ``from autoapply.execute.submitter.dom_batch import X``
keep working.

New code should import from :mod:`.dom` directly:

    from autoapply.execute.submitter.dom import batch_resolve_dom_fields

See the package docstring in ``dom/__init__.py`` for the layout.
"""

from __future__ import annotations

from .dom import (
    _DEGREE_OPTION_PREFERENCES,
    _DISCIPLINE_OPTION_PREFERENCES,
    _DomField,
    _SCHOOL_OPTION_PREFERENCES,
    _US_STATE_LABELS,
    _education_patterns_for_label,
    _match_preferred_option,
    batch_resolve_dom_fields,
    collect_empty_required_fields,
)

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
