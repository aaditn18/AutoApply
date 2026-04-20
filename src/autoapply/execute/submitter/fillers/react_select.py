"""React-Select / ARIA combobox filling with a 6-step match ladder.

The core function :func:`fill_combobox` is the single hottest code path
in the submission pipeline — it runs once per dropdown across every
application, and every ATS renders its React-Select slightly
differently. The function body is long on purpose: it's a waterfall of
pragmatic fallbacks, each of which fixed a specific misfire observed
in production.

Match ladder (tried in order; first to match wins):

  a. Preferred-pattern match (for education fields) — caller-supplied
     ordered regex list. Fires BEFORE exact/prefix match so UMD
     College Park wins over UMD Baltimore on tenants where both appear.
  a'. Exact case-insensitive text match.
  b. Prefix / substring match.
  c. Normalized token-set match — punctuation-tolerant.
  d. EEO "decline to self-identify" semantic fallback — maps "Decline"
     to "Prefer not to say" / "I don't wish" / etc.

If nothing matches: raise. The ladder deliberately does NOT guess
(Yes/No, first-option, alphabetical) — a wrong answer is worse than a
review-queue entry. The hard-won lesson: the old binary-Yes/No guesser
answered "Yes" for "Do you have GPA of 4+?" against a 3.975 GPA.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from autoapply.rules import load_rules

from ..util import jitter
from .matching import _normalize_tokens


log = logging.getLogger(__name__)


# EEO decline-option synonyms (loaded from state/rules/eeo_semantics.yml).
# Module-level so the YAML parse happens once at import.
_DECLINE_KEYWORDS: tuple[str, ...] = tuple(
    load_rules("eeo_semantics")["decline_keywords"]
)


def fill_combobox(
    page: Any,
    input_el: Any,
    value: str,
    prefer_patterns: tuple[str, ...] | None = None,
) -> None:
    """Type ``value`` into a React-Select / ARIA combobox and pick the match.

    See module docstring for the match ladder.

    ``prefer_patterns`` is an optional ordered tuple of regex strings the
    caller can pass to bias the match toward specific option variants
    (e.g., "University of Maryland - College Park" before
    "University of Maryland - Baltimore"). Preference rules live in
    ``state/rules/education_preferences.yml`` and are looked up by
    :func:`..dom.preferences._education_patterns_for_label`.
    """
    try:
        _open_dropdown(input_el)
        _type_value_with_prefix_delay(input_el, value)

        # Locate options scoped to THIS combobox's listbox.
        aria_controls = input_el.get_attribute("aria-controls") or ""
        options = _locate_options(page, input_el, aria_controls)

        # Async-typeahead poll: options may take longer than our initial
        # jitter to render. Wait up to ~2s for at least one option.
        for _ in range(10):
            if options.count() > 0:
                break
            jitter(0.2, 0.3)

        count = options.count()
        if count == 0:
            options, count = _locate_options_menu_fallback(input_el)
        if count == 0:
            options, count = _locate_options_visible_listbox(page)

        log.info(
            "fill_combobox: typed=%r → %d option(s) found (aria_controls=%r)",
            value, count, aria_controls,
        )

        # Clear-and-retry fallback: short enumerated React-Select
        # dropdowns (pronouns, yes/no questions, GPA-threshold yes/no)
        # often have option text that doesn't contain our bank value
        # verbatim. Typing "He/Him" against ["He/Him", "She/Her", ...]
        # can filter to 0 matches when the search normalization treats
        # "/" specially. Clear the input and retry against the
        # UNFILTERED option list. Bounded to one retry.
        if count == 0:
            try:
                input_el.fill("", timeout=2_000)
                jitter(0.3, 0.6)
            except Exception:
                pass
            options = _locate_options(page, input_el, aria_controls)
            for _ in range(6):
                if options.count() > 0:
                    break
                jitter(0.2, 0.3)
            count = options.count()
            log.info(
                "fill_combobox: cleared filter → %d option(s) visible",
                count,
            )

        if count == 0:
            # Truly no options even unfiltered — press Enter on whatever
            # is typed as a last-resort free-text submission.
            try:
                input_el.press("Enter")
            except Exception:
                pass
            return

        # Snapshot option texts once — cheaper than calling inner_text()
        # repeatedly across all the match passes below.
        texts = _snapshot_texts(options, count)

        v_lower = value.strip().lower()

        def _pick(index: int) -> None:
            _commit_pick(page, input_el, options, index, texts)

        # a. Preferred-pattern match — caller-supplied ordered regexes.
        if prefer_patterns:
            for pat in prefer_patterns:
                rx = re.compile(pat, re.IGNORECASE)
                for i, txt in enumerate(texts):
                    if rx.search(txt):
                        log.info(
                            "fill_combobox: preferred-pattern %r matched %r (idx=%d)",
                            pat, texts[i][:60], i,
                        )
                        _pick(i)
                        return

        # a'. Exact case-insensitive text match.
        for i, txt in enumerate(texts):
            if txt.lower() == v_lower:
                _pick(i)
                return

        # b. Prefix / substring.
        for i, txt in enumerate(texts):
            tl = txt.lower()
            if tl and (tl.startswith(v_lower) or v_lower in tl):
                _pick(i)
                return

        # c. Normalized token-set match — punctuation-tolerant.
        v_tokens = _normalize_tokens(value)
        if len(v_tokens) >= 2:
            best_i: int | None = None
            best_overlap = 0
            for i, txt in enumerate(texts):
                t_tokens = _normalize_tokens(txt)
                if not t_tokens:
                    continue
                if v_tokens.issubset(t_tokens) or (
                    t_tokens.issubset(v_tokens) and len(t_tokens) >= 2
                ):
                    if len(t_tokens) > best_overlap:
                        best_overlap = len(t_tokens)
                        best_i = i
            if best_i is not None:
                _pick(best_i)
                return

        # d. EEO decline fallback.
        if any(kw in v_lower for kw in _DECLINE_KEYWORDS):
            for i, txt in enumerate(texts):
                tl = txt.lower()
                if any(kw in tl for kw in _DECLINE_KEYWORDS):
                    _pick(i)
                    return

        # No deterministic match. We deliberately do NOT invoke any
        # hardcoded semantic fallback here (binary Yes/No, source
        # preference, first-option). A leaked wrong answer is worse
        # than a review-queue entry.
        raise ValueError(
            f"combobox: no matching option for value={value!r} "
            f"(first options: {texts[:5]!r})"
        )

    except Exception as exc:
        log.debug("combobox fill failed for value=%r: %s", value, exc)
        raise


# ─── Internal dropdown-open helpers ─────────────────────────────────────


def _open_dropdown(input_el: Any) -> None:
    """Click to open the React-Select; fall back to ancestor control."""
    try:
        input_el.scroll_into_view_if_needed(timeout=2_000)
    except Exception:
        pass
    try:
        input_el.click(timeout=3_000)
    except Exception:
        # Hidden input that Playwright won't click → focus + ArrowDown.
        try:
            input_el.focus(timeout=2_000)
            input_el.press("ArrowDown")
        except Exception:
            pass
    jitter(0.2, 0.4)

    expanded = ""
    try:
        expanded = (input_el.get_attribute("aria-expanded") or "").lower()
    except Exception:
        pass
    if expanded != "true":
        # Click the ancestor control wrapper — the element that actually
        # owns the open/close behavior in React-Select.
        try:
            ctrl = input_el.locator(
                "xpath=ancestor::*["
                "contains(@class,'select__control') or "
                "contains(@class,'Select__control') or "
                "contains(@class,'-control')][1]"
            )
            if ctrl.count() > 0:
                ctrl.first.click(timeout=2_500)
                jitter(0.2, 0.4)
        except Exception:
            pass


def _type_value_with_prefix_delay(input_el: Any, value: str) -> None:
    """Type the value in two bursts so async typeaheads race-proof.

    School / Country dropdowns often load options asynchronously after a
    minimum typed-prefix (2-3 chars). Rather than typing the full value
    at once (which can race the XHR), we type the first 3 chars, wait
    briefly, then type the rest.
    """
    try:
        input_el.fill("", timeout=2_000)
    except Exception:
        pass
    prefix = value[:3]
    remainder = value[3:]
    input_el.type(prefix, delay=50)
    jitter(0.3, 0.6)
    if remainder:
        input_el.type(remainder, delay=30)
    jitter(0.5, 1.0)


def _locate_options(page: Any, input_el: Any, aria_controls: str) -> Any:
    """Return a locator scoped to THIS combobox's option list.

    Order of preference:
      1. ``aria-controls`` attribute on the input — most reliable.
      2. Descendants of the SAME container as the input (scopes away
         from OTHER React-Selects on the page — e.g., the phone-number
         country-code picker always present on Greenhouse SPA forms).
    """
    if aria_controls:
        return page.locator(f'[id="{aria_controls}"] [role="option"]')
    return input_el.locator(
        "xpath=ancestor::*["
        "contains(@class,'select__container') or "
        "contains(@class,'Select__container') or "
        "contains(@class,'-container')][1]"
        "//*[@role='option']"
    )


def _locate_options_menu_fallback(input_el: Any) -> tuple[Any, int]:
    """Fallback: menu-class descendants within this field's container.

    React-Select renders the menu as a sibling of the control when not
    portaled; some tenants style it with ``Menu`` / ``menu`` class
    names rather than ``role="option"``.
    """
    options = input_el.locator(
        "xpath=ancestor::*[contains(@class,'container')][1]"
        "//*[contains(@class,'menu') or contains(@class,'Menu')]"
        "//*[@role='option' or contains(@id,'option')]"
    )
    for _ in range(5):
        if options.count() > 0:
            break
        jitter(0.2, 0.3)
    return options, options.count()


def _locate_options_visible_listbox(page: Any) -> tuple[Any, int]:
    """Absolute last resort: visible listboxes only, page-wide.

    Skips the always-mounted phone country picker (hidden off-screen).
    """
    options = page.locator('[role="listbox"]:visible [role="option"]')
    for _ in range(3):
        if options.count() > 0:
            break
        jitter(0.2, 0.3)
    return options, options.count()


def _snapshot_texts(options: Any, count: int) -> list[str]:
    """Read up to 80 option texts at once — cheaper than per-match scans."""
    texts: list[str] = []
    for i in range(min(count, 80)):
        try:
            texts.append(options.nth(i).inner_text().strip())
        except Exception:
            texts.append("")
    return texts


def _commit_pick(
    page: Any,
    input_el: Any,
    options: Any,
    index: int,
    texts: list[str],
) -> None:
    """Select the option at ``index`` using a React-Select-compatible
    event sequence, with a keyboard fallback.

    React-Select's primary option-selection handler is ``onMouseDown``
    — NOT onClick. Playwright's ``.click()`` dispatches a full sequence
    (mousedown/mouseup/click) but depending on how React-Select wraps
    the option, the event may not reach the handler in time. We
    explicitly dispatch ``mousedown`` first, then click, which
    reliably triggers selection across v3/v4/v5.

    If the mouse-event path fails (option detached, listbox
    re-rendered between scrape and pick), we fall back to keyboard
    navigation. Some React-Select instances — notably the Greenhouse
    SPA's main form fields — commit via ArrowDown+Enter just fine;
    others (the EEO section on boards.greenhouse.io) only commit via
    the mouse path.
    """
    # Primary: mousedown + click on the target option.
    try:
        opt_el = options.nth(index)
        try:
            opt_el.scroll_into_view_if_needed(timeout=1_000)
        except Exception:
            pass
        opt_el.dispatch_event("mousedown")
        opt_el.dispatch_event("mouseup")
        opt_el.click(timeout=3_000, force=True)
        jitter(0.4, 0.6)
        log.info(
            "fill_combobox: picked index=%d (%r) via mouse",
            index, texts[index] if index < len(texts) else "?",
        )
        return
    except Exception as exc:
        log.debug(
            "fill_combobox: mouse pick failed at index=%d: %s — "
            "falling back to keyboard", index, exc,
        )

    # Fallback: keyboard navigation.
    try:
        input_el.focus(timeout=1_000)
    except Exception:
        pass
    for _ in range(index):
        page.keyboard.press("ArrowDown")
        jitter(0.05, 0.1)
    page.keyboard.press("Enter")
    jitter(0.4, 0.6)
    log.info(
        "fill_combobox: picked index=%d (%r) via keyboard",
        index, texts[index] if index < len(texts) else "?",
    )
