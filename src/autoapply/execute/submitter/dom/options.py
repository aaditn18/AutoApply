"""React-Select detection + option harvesting.

Three scraping modes:

- **Native ``<select>``** — read ``<option>`` children directly; no
  interaction needed.
- **React-Select (sync)** — click to open, wait for the listbox, read
  ``[role="option"]`` text, press Escape to close.
- **React-Select (async typeahead)** — some dropdowns (School, Country
  with 240+ entries) don't render any options until the user types;
  type a seed string first, then scrape.

All Playwright-bound. Returning ``[]`` is the signal to downgrade a
field to text-input mode so the LLM writes a freeform answer and
``fill_combobox`` does the type-and-filter at fill time.

The module also exposes :func:`_close_dropdown_state` — used to blur
the active element between fills so React-Select's onBlur commits the
previous selection before the next mousedown fires.
"""

from __future__ import annotations

import logging
from typing import Any

from ..util import jitter


log = logging.getLogger(__name__)


def _looks_like_react_select(el: Any) -> bool:
    """True when the input is wrapped by a React-Select control.

    Same heuristic as :func:`..field_fill._is_react_select`, duplicated
    here to avoid a cross-module import (:mod:`.field_fill` imports
    ``llm_fallback`` at module load, and keeping the dom/ package's
    import graph clean matters for the orchestrator's startup cost).
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
            "contains(@class,'-control')][1]"
        )
        if anc.count() > 0:
            return True
    except Exception:
        pass
    return False


def _scrape_select_options(page: Any, el: Any, max_opts: int = 80) -> list[str]:
    """Open a React-Select / native-select, capture options, close.

    React-Select renders its menu in a portal on demand. We click the
    input to open, wait briefly, read ``[role='option']`` text, then
    press Escape to close. Native ``<select>`` elements have ``<option>``
    children readable without interaction.

    Async-typeahead handling: some dropdowns (School lookups, Country
    pickers with 10k+ entries) don't populate options until the user
    types. If the initial click+wait gives zero options, we treat the
    field as a freetext target — the LLM returns a string, and our
    ``fill_combobox`` path types + picks from the async-loaded menu at
    fill time. Returning ``[]`` here is the signal for that path.

    Returns a list of option labels (trimmed, empty strings excluded).
    Bounded to ``max_opts`` to keep prompt size under control for 200+
    option lists (e.g., country codes on phone-number pickers).
    """
    # Native <select>: read <option> directly, no click needed.
    try:
        tag = el.evaluate("e => e.tagName.toLowerCase()") or ""
    except Exception:
        tag = ""
    if tag == "select":
        try:
            opts = el.evaluate("""
                (el) => Array.from(el.querySelectorAll('option'))
                    .map(o => (o.textContent || '').trim())
                    .filter(t => t.length > 0)
            """) or []
            return list(opts)[:max_opts]
        except Exception as exc:
            log.debug("dom_batch: native select options failed: %s", exc)
            return []

    # React-Select path.
    try:
        el.scroll_into_view_if_needed(timeout=2_000)
    except Exception:
        pass
    try:
        el.click(timeout=3_000)
    except Exception:
        # Hidden input — try ancestor control click.
        try:
            ctrl = el.locator(
                "xpath=ancestor::*["
                "contains(@class,'select__control') or "
                "contains(@class,'-control')][1]"
            )
            if ctrl.count() > 0:
                ctrl.first.click(timeout=2_500)
        except Exception:
            pass

    jitter(0.3, 0.6)

    # Scope to this combobox's listbox.
    aria_controls = ""
    try:
        aria_controls = el.get_attribute("aria-controls") or ""
    except Exception:
        pass

    if aria_controls:
        options_loc = page.locator(f'[id="{aria_controls}"] [role="option"]')
    else:
        options_loc = el.locator(
            "xpath=ancestor::*["
            "contains(@class,'select__container') or "
            "contains(@class,'-container')][1]"
            "//*[@role='option']"
        )

    # Async-load tolerance.
    for _ in range(8):
        if options_loc.count() > 0:
            break
        jitter(0.2, 0.3)

    out: list[str] = []
    count = options_loc.count()
    for i in range(min(count, max_opts)):
        try:
            txt = options_loc.nth(i).inner_text().strip()
            if txt:
                out.append(txt)
        except Exception:
            continue

    # Close the dropdown so subsequent scrapes don't pick up this one's
    # options.
    try:
        el.press("Escape")
    except Exception:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
    jitter(0.1, 0.3)

    return out


def _scrape_async_typeahead_options(
    page: Any, el: Any, seed_text: str, max_opts: int = 30,
) -> list[str]:
    """Type a seed string into an async-typeahead React-Select, then
    scrape the options that appear.

    Used when the plain ``_scrape_select_options`` pass returns empty
    because the dropdown requires user input before it loads anything
    (Schools/Universities typeahead, Country typeahead, etc.). Typing
    a discriminating prefix (e.g., "univ" for School, "United States"
    for Country) triggers the XHR fetch, and the menu renders
    predictable results.

    Returns a list of option label strings (trimmed, empty-stripped).
    Bounded to ``max_opts`` so the subsequent prompt stays small.
    """
    if not seed_text:
        return []
    # Open the dropdown.
    try:
        el.scroll_into_view_if_needed(timeout=2_000)
    except Exception:
        pass
    try:
        el.click(timeout=3_000)
    except Exception:
        try:
            el.focus(timeout=1_000)
        except Exception:
            pass
    jitter(0.2, 0.4)
    try:
        el.fill("", timeout=2_000)
    except Exception:
        pass
    try:
        el.type(seed_text, delay=40)
    except Exception:
        return []
    # Async load — wait a bit longer here because the XHR fetch takes
    # hundreds of ms on slow backends.
    jitter(0.8, 1.2)

    aria_controls = ""
    try:
        aria_controls = el.get_attribute("aria-controls") or ""
    except Exception:
        pass
    if aria_controls:
        options_loc = page.locator(f'[id="{aria_controls}"] [role="option"]')
    else:
        options_loc = el.locator(
            "xpath=ancestor::*[contains(@class,'-container')][1]"
            "//*[@role='option']"
        )
    for _ in range(8):
        if options_loc.count() > 0:
            break
        jitter(0.2, 0.3)

    out: list[str] = []
    count = options_loc.count()
    for i in range(min(count, max_opts)):
        try:
            txt = options_loc.nth(i).inner_text().strip()
            if txt:
                out.append(txt)
        except Exception:
            continue

    # Clear what we typed so subsequent fills start clean.
    try:
        el.fill("", timeout=1_500)
    except Exception:
        pass
    try:
        el.press("Escape")
    except Exception:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
    jitter(0.1, 0.3)

    return out


def _close_dropdown_state(page: Any) -> None:
    """Blur any focused control before the next fill's mousedown fires.

    Strategy: call ``document.activeElement.blur()`` directly — this
    dispatches the React-Select component's onBlur which finalizes /
    commits any pending selection. We avoid:

      * Tab — moves focus to the next focusable element, which may
        itself be a React-Select input and trigger its own events.
      * Escape — on some React-Select versions cancels the last pick.
      * Mouse click elsewhere — lands on a real element and can trigger
        unintended handlers.

    A direct JS blur is the safest "commit and clear focus" operation
    that doesn't interact with anything else on the page.
    """
    try:
        page.evaluate("""() => {
            const el = document.activeElement;
            if (el && el.blur) el.blur();
        }""")
    except Exception:
        pass
    jitter(0.3, 0.5)
