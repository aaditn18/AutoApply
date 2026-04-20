"""Option-snapping for select / multi_select / checkbox fields.

Given a resolved value like "Maryland" and a spec with options
``["California", "Maryland (MD)", ...]``, we want to pick the exact
option string ("Maryland (MD)") so Playwright's ``select_option``
succeeds downstream.

Match order:
  1. Case-insensitive exact match.
  2. Bidirectional substring match.
  3. ITAR / export-control fallback — when the value is a non-US
     country name and the options list is US-person-status entries,
     pick the "Not currently a US person / Other status" option.

If nothing matches, return the value unchanged — the server will
reject it and we surface the error upstream.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from autoapply.rules import load_rules


if TYPE_CHECKING:
    from autoapply.execute.standard_fields import FieldSpec


# Export-control fallback markers loaded from state/rules/export_control.yml.
# See the rule file for semantics. Lowercase tuples; consumer lowercases
# scraped option text before comparing.
_EXPORT_CONTROL_RULES = load_rules("export_control")
_US_PERSON_MARKERS: tuple[str, ...] = tuple(
    _EXPORT_CONTROL_RULES["us_person_markers"]
)
_OTHER_MARKERS: tuple[str, ...] = tuple(_EXPORT_CONTROL_RULES["other_markers"])


def _snap_to_option(value: str, spec: "FieldSpec") -> str:
    """Snap ``value`` to the closest literal option in ``spec.options``.

    Non-select kinds and option-less specs are returned unchanged.
    """
    if spec.kind not in ("select", "multi_select", "checkbox"):
        return value
    if not spec.options:
        return value
    v = value.strip().lower()

    # 1. Exact match.
    for opt in spec.options:
        if opt.strip().lower() == v:
            return opt
    # 2. Substring (either direction).
    for opt in spec.options:
        if v in opt.lower() or opt.lower() in v:
            return opt

    # 3. ITAR / export-control fallback. When the value is a non-US
    # country name (India, UK, ...) AND the options list looks like
    # US-person-status entries, substring match will miss and we pick
    # the "Not currently / Other status" option explicitly.
    has_us_options = any(
        any(m in opt.lower() for m in _US_PERSON_MARKERS)
        for opt in spec.options
    )
    if has_us_options:
        for opt in spec.options:
            ol = opt.lower()
            if any(m in ol for m in _OTHER_MARKERS):
                return opt

    # Nothing matches — return the original; the server will reject.
    return value
