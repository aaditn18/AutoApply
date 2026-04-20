"""DOM walk for empty required fields.

The single public function :func:`collect_empty_required_fields` walks
the page's visible inputs and returns a list of :class:`_DomField`
records for the ones that:

  * are ``<input>`` (text-like), ``<textarea>``, or ``<select>``;
  * are visible on screen;
  * are marked required (``required``, ``aria-required=true``, or a
    ``.required`` marker in the ATS's form-group ancestor);
  * don't already have a value (React-Select ``singleValue`` included);
  * aren't in the ``already_filled_keys`` set the caller passes in.

Kind classification happens here: ``select`` for native selects AND
React-Select inputs (detected via :func:`._looks_like_react_select`),
``textarea``, ``checkbox`` for standalone consent boxes, and ``text``
for everything else.

All Playwright-bound. This module doesn't touch the classifier or
profile — it's pure DOM collection.
"""

from __future__ import annotations

import logging
from typing import Any

from .fields import _DomField, _SUPPORTED_INPUT_TYPES
from .options import _looks_like_react_select


log = logging.getLogger(__name__)


def collect_empty_required_fields(
    page: Any, already_filled_keys: set[str]
) -> list[_DomField]:
    """Walk the DOM for required fields that are still empty.

    Only scans fields visible on screen — collapsed sections (rarely
    required) are skipped since we can't interact with them reliably
    anyway. Dedupes by ``id`` / ``name``.

    Options scraping for React-Select happens lazily (later, in
    :func:`..options._scrape_select_options`) — not here — because
    opening a dropdown is slow and we only want to do it if we're
    going to send the field to the LLM.
    """
    collected: dict[str, _DomField] = {}

    def _tag(el: Any) -> str:
        try:
            return (
                el.evaluate("e => e.tagName && e.tagName.toLowerCase()") or ""
            )
        except Exception:
            return ""

    def _is_required(el: Any) -> bool:
        try:
            if el.evaluate("e => e.required") is True:
                return True
            attr = el.get_attribute("aria-required") or ""
            if (attr or "").lower() == "true":
                return True
            # Greenhouse / Lever often put a ``.required`` marker in the
            # containing form group.
            has_marker = el.evaluate(
                "e => !!(e.closest('.application-question')?.querySelector('.required'))"
            )
            return bool(has_marker)
        except Exception:
            return False

    def _has_value(el: Any, tag: str) -> bool:
        """True if the field already has a usable non-placeholder value.

        For React-Select, also reads the ``singleValue`` render so we
        don't re-fill a combobox that Stage-1 or earlier passes already
        resolved. The check mirrors the one in ``diagnostics.py``.
        """
        try:
            if tag == "select":
                raw = el.evaluate("e => e.value") or ""
                return bool(raw) and raw.strip().lower() not in (
                    "", "select", "select...", "choose", "choose...",
                )
            # Read the React-Select singleValue text too — a filled
            # combobox has empty .value but a visible singleValue span.
            info = el.evaluate("""
                (el) => {
                    const v = el.value || '';
                    const wrapper = el.closest(
                        '[class*="container"], [class*="Select"], '
                        + '[class*="-control"]'
                    );
                    let sv = '';
                    if (wrapper) {
                        const node = wrapper.querySelector(
                            '[class*="singleValue"], '
                            + '[class*="single-value"], '
                            + '[class*="singleval"]'
                        );
                        if (node) sv = (node.innerText || '').trim();
                    }
                    return {v: v.trim(), sv: sv};
                }
            """) or {}
            val = (info.get("v") or "").strip()
            sv = (info.get("sv") or "").strip()
            return bool(val) or bool(sv)
        except Exception:
            return False

    def _label_of(el: Any) -> str:
        """Priority: aria-label → aria-labelledby → <label for> →
        ancestor <label> → placeholder."""
        try:
            al = (el.get_attribute("aria-label") or "").strip()
            if al:
                return al
        except Exception:
            pass
        try:
            alb = (el.get_attribute("aria-labelledby") or "").strip()
            if alb:
                for ref in alb.split():
                    loc = page.locator(f'[id="{ref}"]')
                    if loc.count() > 0:
                        txt = loc.first.inner_text().strip()
                        if txt:
                            return txt
        except Exception:
            pass
        try:
            eid = el.get_attribute("id") or ""
            if eid:
                lbl = page.locator(f'label[for="{eid}"]')
                if lbl.count() > 0:
                    t = lbl.first.inner_text().strip()
                    if t:
                        return t
        except Exception:
            pass
        try:
            anc = el.locator("xpath=ancestor::label[1]")
            if anc.count() > 0:
                t = anc.first.inner_text().strip()
                if t:
                    return t
        except Exception:
            pass
        try:
            return (el.get_attribute("placeholder") or "").strip()
        except Exception:
            return ""

    # Enumerate candidate elements. Selectors deliberately include
    # ``[id]``-only fields (new Greenhouse SPA) and textarea/select.
    # Checkboxes are included so we can auto-check single required
    # consent / T&C boxes (GDPR, privacy, arbitration). Multi-select
    # checkbox groups (state lists, "which locations") are handled as
    # a separate post-processing pass.
    selectors = (
        "input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=file])",
        "select",
        "textarea",
    )
    elements: list[Any] = []
    for sel in selectors:
        try:
            elements.extend(page.locator(sel).all())
        except Exception as exc:
            log.debug("dom_batch: enumerate %r failed: %s", sel, exc)

    for el in elements:
        try:
            eid = el.get_attribute("id") or ""
            ename = el.get_attribute("name") or ""
            dedup_key = eid or ename
            if not dedup_key or dedup_key in collected:
                continue
            if dedup_key in already_filled_keys:
                continue

            tag = _tag(el)
            input_type = (el.get_attribute("type") or "").lower()

            # Checkboxes get a dedicated kind and filler — the element
            # ISN'T skipped here but its kind becomes "checkbox" below.
            # Radios and file inputs are always skipped.
            if tag == "input" and input_type == "radio":
                continue
            if tag == "input" and input_type == "file":
                continue
            if tag == "input" and input_type not in _SUPPORTED_INPUT_TYPES \
                    and input_type != "checkbox":
                continue

            # Visibility gate.
            try:
                if not el.is_visible(timeout=500):
                    continue
            except Exception:
                continue

            # Required gate.
            if not _is_required(el):
                continue

            # Already has a value → skip. For checkboxes, "has value"
            # means "checked" (el.checked).
            if tag == "input" and input_type == "checkbox":
                try:
                    if el.evaluate("e => e.checked") is True:
                        continue
                except Exception:
                    pass
            elif _has_value(el, tag):
                continue

            label = _label_of(el)
            if not label:
                continue  # no way for LLM to reason about it

            # Kind classification.
            if tag == "input" and input_type == "checkbox":
                # Standalone required checkbox (T&C, GDPR, privacy).
                # Group-style checkbox lists (multi-state selection)
                # would also land here but each checkbox gets its own
                # entry with the surrounding label; the LLM will
                # answer Yes/No per checkbox based on context.
                kind = "checkbox"
            elif tag == "select":
                kind = "select"
            elif tag == "textarea":
                kind = "textarea"
            else:
                # Could be React-Select; detection happens at option-
                # scrape time. For now, mark as "text" and upgrade to
                # "select" if we detect the wrapper.
                kind = "text"
                if _looks_like_react_select(el):
                    kind = "select"

            collected[dedup_key] = _DomField(
                element_id=dedup_key,
                label=label,
                kind=kind,
                options=[],  # scraped lazily below
                locator=el,
            )
        except Exception as exc:
            log.debug("dom_batch: element scan error: %s", exc)

    return list(collected.values())
