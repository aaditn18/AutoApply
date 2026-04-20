"""Backwards-compatible re-export shim.

The form-fill logic used to live here as a single 889-LOC file. The
code now lives in the :mod:`.fillers` subpackage, one module per fill
strategy (``matching``, ``detect``, ``native_select``, ``react_select``,
``checkbox_radio``, ``dispatch``). This module re-exports the
public + test-accessed names so existing imports
``from autoapply.execute.submitter.field_fill import X`` keep working.

New code should import from :mod:`.fillers` directly:

    from autoapply.execute.submitter.fillers import fill_field, fill_combobox

See the package docstring in ``fillers/__init__.py`` for the layout.
"""

from __future__ import annotations

from .fillers import (
    _combobox_has_value,
    _is_react_select,
    _looks_like_placeholder,
    _normalize_tokens,
    _read_input_label,
    fill_combobox,
    fill_field,
    fill_radio,
    fill_select,
)

__all__ = [
    "fill_field",
    "fill_combobox",
    "fill_select",
    "fill_radio",
    "_is_react_select",
    "_combobox_has_value",
    "_read_input_label",
    "_normalize_tokens",
    "_looks_like_placeholder",
]
