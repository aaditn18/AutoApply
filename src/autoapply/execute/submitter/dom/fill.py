"""Per-field fill dispatch for Stage-2.

:func:`_fill_one` takes a resolved ``(field, value)`` pair and dispatches
to the correct Playwright filler:

- ``kind="checkbox"`` → ``check()`` / ``uncheck()`` based on truthy token
- native ``<select>`` → :func:`..field_fill.fill_select`
- React-Select → :func:`..field_fill.fill_combobox` with
  ``prefer_patterns`` for education fields
- plain text / textarea → ``.fill()`` with a combobox retry fallback

Critical: we **re-locate** the element fresh from the DOM rather than
using ``f.locator`` captured at scrape time. React-Select widgets
unmount-and-remount when adjacent fields commit (observed on
jjsnackfoods: filling Degree re-rendered the whole education group,
leaving Discipline's captured locator pointing at a detached DOM node
— subsequent click/type fell through to the next form input, LinkedIn
Profile). Re-locating by ``id``/``name`` guarantees a live handle.
"""

from __future__ import annotations

import logging
from typing import Any

from .fields import _DomField
from .preferences import _education_patterns_for_label


log = logging.getLogger(__name__)


def _fill_one(page: Any, f: _DomField, value: str) -> bool:
    """Fill one scraped field with the resolved value.

    Returns True on apparent success; False if every strategy raised.
    """
    # Import here — field_fill imports llm_fallback at module load, and
    # we want to keep dom/'s import graph simple.
    from ..field_fill import _is_react_select, fill_combobox, fill_select

    # Re-resolve a fresh locator by id/name. Falls back to the original
    # locator only if re-resolution finds nothing (e.g., the field truly
    # disappeared from the DOM between scrape and fill).
    fresh_el = None
    try:
        by_id = page.locator(f'[id="{f.element_id}"]')
        if by_id.count() > 0:
            fresh_el = by_id.first
        else:
            by_name = page.locator(f'[name="{f.element_id}"]')
            if by_name.count() > 0:
                fresh_el = by_name.first
    except Exception:
        pass
    el = fresh_el if fresh_el is not None else f.locator

    try:
        tag = el.evaluate("e => e.tagName.toLowerCase()") or ""
    except Exception:
        tag = ""

    try:
        if f.kind == "checkbox":
            _YES_TOKENS = {
                "yes", "y", "true", "1", "agree", "i agree", "accept",
                "i accept", "consent", "i consent", "confirm", "i confirm",
                "on", "checked",
            }
            v = (value or "").strip().lower()
            truthy = v in _YES_TOKENS or v.startswith("yes")
            try:
                el.scroll_into_view_if_needed(timeout=1_500)
            except Exception:
                pass
            if truthy:
                el.check(timeout=2_500, force=True)
            else:
                el.uncheck(timeout=2_500, force=True)
            return True

        # Education fields (School / Degree / Major / Discipline) get
        # preferred-pattern lists so fill_combobox picks the College
        # Park UMD campus (not Baltimore) and the correct degree/major
        # canonical form across tenants that list multiple synonyms.
        prefer_patterns = _education_patterns_for_label(f.label)

        if tag == "select":
            fill_select(el, value)
            return True
        if _is_react_select(el):
            fill_combobox(page, el, value, prefer_patterns=prefer_patterns)
            return True
        # Plain text fallback.
        try:
            el.fill(value, timeout=3_000)
            actual = (el.input_value(timeout=500) or "").strip()
            if not actual:
                fill_combobox(page, el, value, prefer_patterns=prefer_patterns)
        except Exception:
            fill_combobox(page, el, value, prefer_patterns=prefer_patterns)
        return True
    except Exception as exc:
        log.debug(
            "dom_batch: fill failed for %s (%r): %s",
            f.element_id, (f.label or "")[:50], exc,
        )
        return False
