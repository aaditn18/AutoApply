"""Playwright probes — React-Select detection, value readback, label lookup.

Small DOM-introspection helpers. Each is a short try/except wrapper
around ``el.get_attribute`` / ``el.evaluate`` / ``el.locator`` — the
design intent is that every probe degrades gracefully to a sensible
default (False / empty string) rather than raising, because all of
them run against potentially-stale or detached elements.
"""

from __future__ import annotations

from typing import Any


def _is_react_select(el: Any) -> bool:
    """True when the element is (part of) a React-Select widget.

    React-Select renders several variations over its versions:

      * v5+  — the inner ``<input>`` has ``role="combobox"`` directly.
      * v3/4 — the ``<input>`` has no ARIA role but an ancestor ``<div>``
               has a class containing ``select__control`` or
               ``Select__control`` (CSS Modules generate names like
               ``react-select__control--is-focused``).
      * Some tenants style react-select with custom class prefixes
        (``css-1x3vp7r-control`` from Emotion); those still usually
        include the literal token ``control``.

    We err on the side of detecting — the combobox flow handles both
    the real combobox and the plain-text case (it types and picks the
    best match), so a false positive costs a click + re-type at worst.
    """
    try:
        role = (el.get_attribute("role") or "").lower()
        if role == "combobox":
            return True
    except Exception:
        return False
    try:
        anc = el.locator(
            "xpath=ancestor::*["
            "contains(@class,'select__control') or "
            "contains(@class,'Select__control') or "
            "contains(@class,'react-select') or "
            "contains(@class,'-control')"
            "][1]"
        )
        if anc.count() > 0:
            return True
    except Exception:
        pass
    return False


def _combobox_has_value(input_el: Any) -> bool:
    """True if the React-Select wrapping ``input_el`` currently displays
    a selected value (non-placeholder ``singleValue`` span).

    Used by :func:`..react_select.fill_combobox` to verify that a
    keyboard pick actually committed into component state before moving
    on. Works across React-Select v3/v4/v5 by probing multiple
    ``singleValue`` class-name conventions (Emotion hashes, CSS Modules,
    plain classes).
    """
    try:
        info = input_el.evaluate("""
            (el) => {
                const wrapper = el.closest(
                    '[class*="container"], [class*="Select"], '
                    + '[class*="-control"]'
                );
                if (!wrapper) return {};
                const sv = wrapper.querySelector(
                    '[class*="singleValue"], '
                    + '[class*="single-value"], '
                    + '[class*="singleval"]'
                );
                const txt = (sv && (sv.innerText || sv.textContent)) || '';
                return {sv: txt.trim()};
            }
        """) or {}
        return bool((info.get("sv") or "").strip())
    except Exception:
        return False


def _read_input_label(page: Any, el: Any) -> str:
    """Best-effort label lookup for a form input.

    Priority:
      1. ``aria-label``
      2. ``aria-labelledby`` (space-separated list of element ids)
      3. ``<label for="id">``
      4. Ancestor ``<label>``
      5. ``placeholder``

    Used as the question text when we hand the field to the LLM
    fallback in ``fill_select`` — without a label, the LLM has no way
    to reason about which option fits.

    Mirrors :func:`autoapply.execute.submitter.label_fallback._label_for`
    — kept separate to avoid a circular import.
    """
    try:
        al = (el.get_attribute("aria-label") or "").strip()
        if al:
            return al
    except Exception:
        pass
    try:
        alb = (el.get_attribute("aria-labelledby") or "").strip()
        if alb:
            parts: list[str] = []
            for ref_id in alb.split():
                loc = page.locator(f'[id="{ref_id}"]')
                if loc.count() > 0:
                    try:
                        parts.append(loc.first.inner_text().strip())
                    except Exception:
                        pass
            joined = " ".join(p for p in parts if p)
            if joined:
                return joined
    except Exception:
        pass
    try:
        el_id = el.get_attribute("id") or ""
        if el_id:
            lbl = page.locator(f'label[for="{el_id}"]')
            if lbl.count() > 0:
                txt = lbl.first.inner_text().strip()
                if txt:
                    return txt
    except Exception:
        pass
    try:
        anc = el.locator("xpath=ancestor::label[1]")
        if anc.count() > 0:
            txt = anc.first.inner_text().strip()
            if txt:
                return txt
    except Exception:
        pass
    try:
        ph = (el.get_attribute("placeholder") or "").strip()
        if ph:
            return ph
    except Exception:
        pass
    return ""
