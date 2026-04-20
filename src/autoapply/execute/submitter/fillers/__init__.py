"""Field-fill strategies — one module per fill type.

The old 889-LOC ``field_fill.py`` mixed six concerns into one file:

  * dispatch (pick the right filler for a given field name)
  * React-Select detection
  * React-Select (``fill_combobox``) with a 6-step fallback ladder
  * native ``<select>`` filling
  * radio / checkbox filling
  * string matching utilities (``_normalize_tokens``, ``_looks_like_placeholder``)

Splitting them makes each concern independently testable and keeps the
React-Select ladder, which was inherently messy, confined to its own
file rather than dominating a 900-LOC module.

Public surface (what callers import):

  from .fillers import fill_field, fill_combobox, fill_select, fill_radio

The old ``field_fill`` module remains as a re-export shim so legacy
imports (``from ...field_fill import fill_field``) keep working.

Layout
------

  matching.py     — ``_normalize_tokens``, ``_looks_like_placeholder``.
                    Pure string helpers. No Playwright.
  detect.py       — ``_is_react_select``, ``_combobox_has_value``,
                    ``_read_input_label``. Playwright-bound probes.
  native_select.py — ``fill_select`` for native ``<select>``.
  react_select.py — ``fill_combobox`` for React-Select / ARIA combobox.
  checkbox_radio.py — ``fill_radio``.
  dispatch.py     — ``fill_field`` — the unified entry point.
"""

from .checkbox_radio import fill_radio
from .detect import _combobox_has_value, _is_react_select, _read_input_label
from .dispatch import fill_field
from .matching import _looks_like_placeholder, _normalize_tokens
from .native_select import fill_select
from .react_select import fill_combobox

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
