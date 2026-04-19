"""Form field filling — select / radio / checkbox / input / combobox.

`fill_field` is the unified entry point; it dispatches to the right
specialized filler based on what it finds under the given ``name`` / ``id``.
"""

from __future__ import annotations

import logging
from typing import Any

from .util import jitter


log = logging.getLogger(__name__)


def fill_field(page: Any, name: str, value: str) -> None:
    """Fill one form field identified by its ``name`` (or ``id``) attribute.

    Strategy (in order):
    1. ``<select>``          → select_option by label text, then value attribute.
    2. ``<input type=radio>`` → check radio whose label text or value matches.
    3. ``<input type=checkbox>`` → check/uncheck based on truthy value string.
    4. Everything else        → fill() (text, textarea, email, number …).

    All selectors try ``[name="..."]`` first, then fall back to ``[id="..."]``
    because the new Greenhouse job-boards SPA omits ``name`` attributes and
    identifies fields by ``id`` only.
    """
    def _loc(attr_selector: str) -> Any:
        """Return the first matching locator using name= then id= fallback."""
        by_name = page.locator(f'[name="{name}"]{attr_selector}')
        if by_name.count() > 0:
            return by_name
        # id= fallback (new Greenhouse job-boards)
        by_id = page.locator(f'[id="{name}"]{attr_selector}')
        if by_id.count() > 0:
            return by_id
        # Some Greenhouse checkboxes append [] to the id: id="question_123[]"
        by_id_arr = page.locator(f'[id="{name}[]"]{attr_selector}')
        if by_id_arr.count() > 0:
            return by_id_arr
        return None

    # 1. <select> -------------------------------------------------------
    sel_loc = _loc("") or page.locator("__never__")  # noqa: F841 (kept for future)
    # Narrow to actual <select> elements.
    actual_sel = page.locator(f'select[name="{name}"], select[id="{name}"]')
    if actual_sel.count() > 0:
        fill_select(actual_sel.first, value)
        return

    # 2. Radio buttons --------------------------------------------------
    radio_name = page.locator(f'input[type="radio"][name="{name}"]')
    radio_id = page.locator(f'input[type="radio"][id*="{name}"]')
    radio_group = radio_name if radio_name.count() > 0 else radio_id
    if radio_group.count() > 0:
        fill_radio(page, radio_group, name, value)
        return

    # 3. Checkbox -------------------------------------------------------
    cb_name = page.locator(f'input[type="checkbox"][name="{name}"]')
    cb_id = page.locator(f'input[type="checkbox"][id="{name}"]')
    cb_loc = cb_name if cb_name.count() > 0 else cb_id
    if cb_loc.count() > 0:
        truthy = value.strip().lower() in ("yes", "true", "1", "on")
        if truthy:
            cb_loc.first.check(timeout=3_000)
        else:
            cb_loc.first.uncheck(timeout=3_000)
        return

    # 4. Text / textarea / number / email / tel -------------------------
    # Try [name=], then [id=], then [id="name[]"], then case-insensitive name.
    for selector in (
        f'[name="{name}"]',
        f'[id="{name}"]',
        f'[id="{name}[]"]',
        # Case-insensitive CSS attribute selector (CSS4 "i" flag).
        # Handles Lever's camelCase URL fields: urls[LinkedIn] vs urls[linkedin].
        f'[name="{name}" i]',
    ):
        try:
            loc = page.locator(selector)
            if loc.count() > 0:
                el = loc.first
                # React-Select detection — the new job-boards.greenhouse.io
                # SPA renders EVERY question (including simple Yes/No) as a
                # React-Select combobox. Detect via role, ancestor class,
                # or a post-fill verification.
                if _is_react_select(el):
                    fill_combobox(page, el, value)
                    return
                # Plain text/textarea/email/tel — try .fill() first, then
                # verify the value stuck. React-Select widgets whose inputs
                # don't have role="combobox" or a detectable ancestor class
                # ignore direct .fill() and leave the value empty; in that
                # case, fall back to the combobox flow.
                try:
                    el.fill(value, timeout=5_000)
                    # Verify the fill stuck (value read-back). If empty, the
                    # element is probably a React-Select wrapper; retry via
                    # fill_combobox. 500 ms budget — just the read.
                    actual = (el.input_value(timeout=500) or "").strip()
                    if not actual:
                        log.debug(
                            "fill_field: .fill() didn't stick for name=%r — "
                            "retrying as combobox", name,
                        )
                        fill_combobox(page, el, value)
                except Exception:
                    # .fill() / input_value() can raise on non-standard
                    # widgets. Fall back to combobox flow.
                    fill_combobox(page, el, value)
                return
        except Exception:
            continue

    # Nothing found — will be recorded as a fill error by the caller.
    raise ValueError(f"no element found for name/id={name!r}")


def _is_react_select(el: Any) -> bool:
    """True when the element is (part of) a React-Select widget.

    React-Select renders several variations over its versions:
      * v5+  — the inner ``<input>`` has ``role="combobox"`` directly.
      * v3/4 — the ``<input>`` has no ARIA role but an ancestor ``<div>``
               has a class containing ``select__control`` or
               ``Select__control`` (CSS Modules generate names like
               ``react-select__control--is-focused``).
      * Some tenants style react-select with custom class prefixes
        (``css-1x3vp7r-control`` from Emotion); those are trickier but
        still usually include the literal token ``control``.

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
    # Ancestor class probe — find any ancestor div whose class string
    # includes "select__control", "Select__control", or ends in "-control".
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


def fill_combobox(
    page: Any,
    input_el: Any,
    value: str,
    prefer_patterns: tuple[str, ...] | None = None,
) -> None:
    """Handle a React-Select / ARIA combobox: type the value then click the
    best matching option from the associated ``[role='listbox']`` dropdown.

    React-Select renders each combobox's option list inside a portal element
    at the document root. The input's ``aria-controls`` attribute points to
    the specific listbox ID (e.g. ``react-select-country-listbox``). We scope
    the option search to that listbox to avoid accidentally clicking options
    belonging to a different combobox.

    Strategy:
      1. Click the input to open the dropdown.
      2. Clear + re-type the value (triggers client-side filtering).
      3. Match ladder (matches :func:`fill_select` so both kinds of
         dropdown behave the same way):
         a. Exact text match.
         b. Prefix / substring match.
         c. Normalized token-set match (handles punctuation diffs).
         d. EEO "decline / prefer not" semantic fallback.
         e. HOW_HEARD_ABOUT "source question" fallback — prefer
            "Other", then "LinkedIn", then first non-placeholder option.
      4. If nothing matches, raise — the caller logs and the diagnostic
         dump shows the unfilled field. **Never clicks blindly**; the
         previous "click the first option" fallback had a bad habit of
         picking "Male" for Gender when the decline option was "Prefer
         not to say".
    """
    try:
        # Open the dropdown. Try scrolling into view + clicking the input
        # first. If aria-expanded doesn't flip to "true", fall back to
        # clicking the ancestor ``.select__control`` wrapper — some
        # React-Select versions only respond to wrapper clicks.
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
            # Click the ancestor control wrapper — the element that
            # actually owns the open/close behavior in React-Select.
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

        # Clear existing content then type the value so filtering fires.
        try:
            input_el.fill("", timeout=2_000)
        except Exception:
            pass
        # School / Country dropdowns often load options *asynchronously*
        # after a minimum typed-prefix (2–3 chars). Rather than typing the
        # full value at once (which can race the XHR), type the first few
        # chars, wait briefly, then type the rest.
        prefix = value[:3]
        remainder = value[3:]
        input_el.type(prefix, delay=50)
        jitter(0.3, 0.6)
        if remainder:
            input_el.type(remainder, delay=30)
        jitter(0.5, 1.0)  # final wait for option render

        # Scope to THIS combobox's listbox. Order of preference:
        #   1. ``aria-controls`` attribute on the input — most reliable.
        #   2. Descendants of the SAME container (``select__container``) as
        #      the input. Scopes away from OTHER React-Selects on the page
        #      (e.g., the phone-number country-code picker always present
        #      on Greenhouse SPA forms — its listbox is page-wide-visible
        #      even when the field we want is closed).
        #   3. Only-if-visible page-wide ``[role='listbox']`` as a last
        #      resort (filtered with ``:visible`` to avoid hidden pickers).
        aria_controls = input_el.get_attribute("aria-controls") or ""
        if aria_controls:
            options = page.locator(f'[id="{aria_controls}"] [role="option"]')
        else:
            options = input_el.locator(
                "xpath=ancestor::*["
                "contains(@class,'select__container') or "
                "contains(@class,'Select__container') or "
                "contains(@class,'-container')][1]"
                "//*[@role='option']"
            )

        # Async-typeahead poll: options may take longer than our initial
        # jitter to render. Wait up to ~2s for at least one option.
        for _ in range(10):
            if options.count() > 0:
                break
            jitter(0.2, 0.3)

        count = options.count()
        if count == 0:
            # Fallback: descendants of this input's container, under any
            # ``[class*="menu"]`` element (React-Select renders the menu
            # in a sibling of the control when not portaled).
            options = input_el.locator(
                "xpath=ancestor::*[contains(@class,'container')][1]"
                "//*[contains(@class,'menu') or contains(@class,'Menu')]"
                "//*[@role='option' or contains(@id,'option')]"
            )
            for _ in range(5):
                if options.count() > 0:
                    break
                jitter(0.2, 0.3)
            count = options.count()

        if count == 0:
            # Absolute last resort: visible listboxes only. Skips the
            # always-mounted phone country picker (hidden off-screen).
            options = page.locator('[role="listbox"]:visible [role="option"]')
            for _ in range(3):
                if options.count() > 0:
                    break
                jitter(0.2, 0.3)
            count = options.count()

        log.info(
            "fill_combobox: typed=%r → %d option(s) found (aria_controls=%r)",
            value, count, aria_controls,
        )

        # Clear-and-retry fallback: short enumerated React-Select
        # dropdowns (pronouns, yes/no questions, GPA-threshold yes/no)
        # often have option text that doesn't contain our bank value
        # verbatim. Typing "He/Him" against options ["He/Him", "She/Her",
        # ...] can filter to 0 matches when the search normalization
        # treats "/" specially. In that case, clear the input and
        # retry against the UNFILTERED option list. Bounded to one
        # retry to avoid infinite loops.
        if count == 0:
            try:
                input_el.fill("", timeout=2_000)
                jitter(0.3, 0.6)
            except Exception:
                pass
            # Re-scope options since the listbox may have re-rendered.
            if aria_controls:
                options = page.locator(f'[id="{aria_controls}"] [role="option"]')
            else:
                options = input_el.locator(
                    "xpath=ancestor::*["
                    "contains(@class,'select__container') or "
                    "contains(@class,'-container')][1]"
                    "//*[@role='option']"
                )
            for _ in range(6):
                if options.count() > 0:
                    break
                jitter(0.2, 0.3)
            count = options.count()
            log.info(
                "fill_combobox: cleared filter → %d option(s) visible",
                count,
            )
            # Refresh the texts snapshot since options changed.
            texts = []
            for i in range(min(count, 200)):
                try:
                    texts.append(options.nth(i).inner_text().strip())
                except Exception:
                    texts.append("")

        if count == 0:
            # Truly no options even unfiltered — press Enter on whatever
            # is typed as a last-resort free-text submission.
            try:
                input_el.press("Enter")
            except Exception:
                pass
            return

        # React-Select quirk: ``.click()`` on a ``[role="option"]`` often
        # fires the wrong event sequence and leaves the component's
        # internal state unchanged (we see the listbox close but the
        # selected value is never set). The reliable cross-version
        # selection path is via keyboard: navigate to the intended
        # option with ArrowDown, then press Enter. React-Select's
        # onKeyDown handler sets state correctly.
        #
        # For the simple cases — exact match or first filtered option —
        # the first option is already highlighted by React-Select's own
        # filter, so a single Enter press selects it. For more
        # complicated cases (decline fallback, source-question fallback,
        # token-set match), we count how far down the list the match is
        # and press ArrowDown that many times.

        # Snapshot option texts once — cheaper than calling inner_text()
        # repeatedly across all the match passes below.
        texts: list[str] = []
        for i in range(min(count, 80)):
            try:
                texts.append(options.nth(i).inner_text().strip())
            except Exception:
                texts.append("")

        v_lower = value.strip().lower()

        def _pick(index: int) -> None:
            """Select the option at ``index`` using a React-Select-compatible
            event sequence, with a keyboard fallback.

            React-Select's primary option-selection handler is
            ``onMouseDown`` — NOT onClick. Playwright's ``.click()``
            dispatches a full sequence (mousedown/mouseup/click) but
            depending on how React-Select wraps the option, the event
            may not reach the handler in time. We explicitly dispatch
            ``mousedown`` first, then click, which reliably triggers
            selection across v3/v4/v5.

            If the mouse-event path fails (option detached, listbox
            re-rendered between scrape and pick), we fall back to
            keyboard navigation. Some React-Select instances — notably
            the Greenhouse SPA's main form fields — commit via
            ArrowDown+Enter just fine; others (the EEO section on
            boards.greenhouse.io) only commit via the mouse path.
            """
            # Primary: mousedown + click on the target option.
            try:
                opt_el = options.nth(index)
                try:
                    opt_el.scroll_into_view_if_needed(timeout=1_000)
                except Exception:
                    pass
                # React-Select listens to mousedown (not click) for option
                # selection. Dispatch mousedown explicitly to ensure the
                # handler fires even when a subsequent click event might
                # be suppressed by a state change.
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

        # a. Preferred-pattern match — callers can pass ``prefer_patterns``
        #    (an ordered tuple of regex strings) when they want a specific
        #    option variant above raw exact/prefix match. Used for School
        #    (UMD College Park > UMD Baltimore > generic UMD), Degree
        #    (Bachelor of Science > B.S. > Bachelor's Degree), and Major
        #    (Computer Science > Computer and Information Sciences) to
        #    disambiguate multi-option matches like "University of
        #    Maryland" that would otherwise alphabetically pick Baltimore.
        #    Fires BEFORE exact/prefix match so the preferred variant
        #    wins even when multiple options match.
        if prefer_patterns:
            import re as _re
            for pat in prefer_patterns:
                rx = _re.compile(pat, _re.IGNORECASE)
                for i, txt in enumerate(texts):
                    if rx.search(txt):
                        log.info(
                            "fill_combobox: preferred-pattern %r matched %r (idx=%d)",
                            pat, texts[i][:60], i,
                        )
                        _pick(i)
                        return

        # a'. Exact text match.
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

        # d. EEO decline fallback — "Decline to self-identify" should
        #    match "Prefer not to say" / "I don't wish to answer" /
        #    "I do not want to answer" / etc. across EEO widget variants.
        _DECLINE_KEYWORDS = (
            "decline",
            "prefer not", "don't prefer", "do not prefer",
            "not wish", "don't wish", "do not wish",
            "not want", "don't want", "do not want",
            "not identify", "don't identify", "do not identify",
            "not disclose", "don't disclose", "do not disclose",
            "choose not", "rather not",
            "not to answer", "not to say",
        )
        if any(kw in v_lower for kw in _DECLINE_KEYWORDS):
            for i, txt in enumerate(texts):
                tl = txt.lower()
                if any(kw in tl for kw in _DECLINE_KEYWORDS):
                    _pick(i)
                    return

        # No deterministic match. We deliberately do NOT invoke any
        # hardcoded semantic fallback here (binary Yes/No, source
        # preference, first-option) — the batched LLM resolver upstream
        # should have resolved this field already. If we've landed here
        # it means either:
        #   1. The upstream batch picked a value that doesn't match any
        #      option verbatim (option-set drifted between resolver
        #      time and DOM-scrape time). → raise; caller logs and the
        #      app routes to review.
        #   2. A DOM-injected SPA field we didn't send to the batch
        #      (e.g. ``Location (City)`` on certain Greenhouse tenants).
        #      → that's what :mod:`.label_fallback` + its own LLM path
        #      handles; it runs AFTER this fill loop.
        # Previously this branch tried to "guess" via rule-based
        # heuristics (binary Yes/No, HOW_HEARD_ABOUT source preference).
        # Those heuristics had edge-case failure modes — most critically
        # the Yes/No guesser would pick "Yes" for "Do you have GPA of
        # 4.0+?" even though our GPA (3.975) doesn't meet the threshold.
        # Removing them is intentional: a leaked wrong answer is worse
        # than a review-queue entry.
        raise ValueError(
            f"combobox: no matching option for value={value!r} "
            f"(first options: {texts[:5]!r})"
        )

    except Exception as exc:
        log.debug("combobox fill failed for value=%r: %s", value, exc)
        raise


def _read_input_label(page: Any, el: Any) -> str:
    """Best-effort label lookup for a form input.

    Mirrors :func:`autoapply.execute.submitter.label_fallback._label_for`
    but is inlined here to avoid a circular import. Priority:
      1. ``aria-label``   2. ``aria-labelledby``   3. ``<label for=id>``
      4. Ancestor ``<label>``   5. ``placeholder``.
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


def _combobox_has_value(input_el: Any) -> bool:
    """True if the React-Select wrapping ``input_el`` currently displays
    a selected value (non-placeholder ``singleValue`` span).

    Used by :func:`fill_combobox` to verify that a keyboard pick actually
    committed into component state before moving on. Works across
    React-Select v3/v4/v5 by probing multiple ``singleValue`` class-name
    conventions (Emotion hashes, CSS Modules, plain classes).
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


def _looks_like_placeholder(text: str) -> bool:
    """True if a dropdown option's text looks like a placeholder row.

    Matches: "Select...", "Select", "Choose...", "Please select", "--",
    "—", "Select an option", "Select one".
    """
    t = (text or "").strip().lower()
    if not t:
        return True
    import re as _re
    return bool(_re.match(
        r"^(select(\.{3})?(\s+(an?\s+option|one))?|choose(\.{3})?|please\s+select|--+|—+)$",
        t,
    ))


def fill_select(sel_el: Any, value: str) -> None:
    """Try several strategies to pick the right ``<select>`` option.

    Matching waterfall:
      1. Exact label match.
      2. Exact ``value=`` attribute match.
      3. Case-insensitive prefix match on option text.
      4. Bidirectional substring match.
      5. **Normalized token-set match** — the one that lets us match
         "University of Maryland, College Park" (profile) against
         "University of Maryland-College Park" (dropdown). Punctuation
         and extra whitespace are stripped before comparing, and a match
         is any option whose tokens are a superset of the value's tokens
         (or vice versa).
      6. EEO "decline / prefer not" semantic fallback.
    """
    v_lower = value.strip().lower()

    # Exact label match.
    try:
        sel_el.select_option(label=value, timeout=3_000)
        return
    except Exception:
        pass

    # Exact value= attribute match.
    try:
        sel_el.select_option(value=value, timeout=3_000)
        return
    except Exception:
        pass

    try:
        opts = sel_el.locator("option").all()
        opt_texts = [o.inner_text().strip() for o in opts]
    except Exception:
        return

    # Case-insensitive prefix match on option text.
    for txt in opt_texts:
        if txt.lower().startswith(v_lower):
            try:
                sel_el.select_option(label=txt, timeout=3_000)
                return
            except Exception:
                pass

    # Substring match in either direction.
    for txt in opt_texts:
        tl = txt.lower()
        if v_lower in tl or tl in v_lower:
            try:
                sel_el.select_option(label=txt, timeout=3_000)
                return
            except Exception:
                pass

    # Normalized token-set match — handles punctuation differences
    # ("University of Maryland, College Park" vs "University of
    # Maryland-College Park"), stray whitespace, and parenthesized
    # qualifiers ("New York (NY)" vs "New York"). Skip trivially short
    # values (<2 tokens) to avoid bogus matches like "Yes" matching
    # "I am yes-sure".
    v_tokens = _normalize_tokens(value)
    if len(v_tokens) >= 2:
        # First pass: find options whose tokens are an EXACT set match
        # (strongest signal — covers punctuation-only diffs).
        for txt in opt_texts:
            if _normalize_tokens(txt) == v_tokens:
                try:
                    sel_el.select_option(label=txt, timeout=3_000)
                    return
                except Exception:
                    pass
        # Second pass: option tokens are a SUPERSET of value tokens
        # (option has extra qualifiers like a state abbreviation).
        # Score by overlap size so longer/more-specific matches win.
        best_txt: str | None = None
        best_overlap = 0
        for txt in opt_texts:
            opt_tokens = _normalize_tokens(txt)
            if not opt_tokens:
                continue
            if v_tokens.issubset(opt_tokens):
                if len(opt_tokens) > best_overlap:
                    best_overlap = len(opt_tokens)
                    best_txt = txt
            elif opt_tokens.issubset(v_tokens) and len(opt_tokens) >= 2:
                # Option is a shorter form of our value
                # ("University of Maryland" option when profile says
                # "University of Maryland, College Park"). Accept only
                # when the option has ≥2 tokens to avoid matching
                # single generic tokens like "other".
                if len(opt_tokens) > best_overlap:
                    best_overlap = len(opt_tokens)
                    best_txt = txt
        if best_txt is not None:
            try:
                sel_el.select_option(label=best_txt, timeout=3_000)
                return
            except Exception:
                pass

    # EEO "decline / prefer not" semantic fallback.
    # When our resolved value is a "decline to identify" variant but the actual
    # select uses different wording (e.g., Lever: "I do not want to answer"),
    # look for any option that conveys the same "no / decline" intent.
    _DECLINE_KEYWORDS = (
        "decline",
        "prefer not", "don't prefer", "do not prefer",
        "not wish", "don't wish", "do not wish",
        "not want", "don't want", "do not want",
        "not identify", "don't identify", "do not identify",
        "not disclose", "don't disclose", "do not disclose",
        "choose not", "rather not",
        "not to answer", "not to say",
    )
    _NO_KEYWORDS = ("no clearance", "none", "no polygraph", "not a protected")
    if any(kw in v_lower for kw in _DECLINE_KEYWORDS + _NO_KEYWORDS):
        for txt in opt_texts:
            tl = txt.lower()
            if any(kw in tl for kw in _DECLINE_KEYWORDS + _NO_KEYWORDS):
                try:
                    sel_el.select_option(label=txt, timeout=3_000)
                    return
                except Exception:
                    pass

    # Final fallback — LLM option picker.
    # Same mechanism as in :func:`fill_combobox`: when all deterministic
    # matchers above miss, hand the question + options + profile to the
    # LLM and let it pick by index. See
    # :func:`autoapply.answers.llm_fallback.pick_option_via_llm`.
    try:
        # We need the page + input-label context. `sel_el` is a locator
        # (``<select>`` element); find its containing page via evaluate_handle
        # trick — or, more simply, read its owning page via ``el.page``
        # if available. Playwright ElementHandle/Locator both expose it.
        page = getattr(sel_el, "page", None)
        if page is None:
            try:
                page = sel_el.locator("xpath=.").page
            except Exception:
                page = None
        question_label = ""
        if page is not None:
            try:
                question_label = _read_input_label(page, sel_el)
            except Exception:
                question_label = ""
        if not question_label:
            question_label = value

        from autoapply.answers.llm_fallback import pick_option_via_llm

        # Skip the placeholder (typically index 0) when building the
        # option list we hand to the LLM — otherwise the model might
        # legitimately pick "Select..." as the best non-answer.
        real_opts: list[str] = []
        real_idx_map: list[int] = []
        for i, txt in enumerate(opt_texts):
            if txt and not _looks_like_placeholder(txt):
                real_opts.append(txt)
                real_idx_map.append(i)
        if len(real_opts) >= 2:
            llm_idx = pick_option_via_llm(
                question=question_label, options=real_opts
            )
            if llm_idx is not None:
                chosen_txt = real_opts[llm_idx]
                try:
                    sel_el.select_option(label=chosen_txt, timeout=3_000)
                    log.info(
                        "fill_select: LLM fallback picked %r for Q=%r",
                        chosen_txt[:60], question_label[:80],
                    )
                    return
                except Exception:
                    pass
    except Exception as exc:
        log.debug("fill_select: LLM fallback failed: %s", exc)


def _normalize_tokens(text: str) -> frozenset[str]:
    """Lowercase + split on non-alphanumeric + drop tiny stopwords.

    Used by the token-set matcher in :func:`fill_select`. Punctuation
    differences collapse: "University of Maryland, College Park" and
    "University of Maryland-College Park" both tokenize to
    {"university", "of", "maryland", "college", "park"}.
    """
    import re as _re
    # Split on anything that's not alphanumeric.
    tokens = _re.findall(r"[A-Za-z0-9]+", (text or "").lower())
    # Drop 1-char tokens (initials, noise).
    return frozenset(t for t in tokens if len(t) >= 2)


def fill_radio(page: Any, radio_group: Any, name: str, value: str) -> None:
    """Check the radio button whose ``value=`` attribute or label text matches value."""
    v_lower = value.strip().lower()

    # By value= attribute (exact, then case-insensitive).
    exact_val = page.locator(f'input[type="radio"][name="{name}"][value="{value}"]')
    if exact_val.count() > 0:
        exact_val.first.check(timeout=3_000)
        return

    # Case-insensitive value= search.
    try:
        for radio in radio_group.all():
            rv = (radio.get_attribute("value") or "").strip().lower()
            if rv == v_lower:
                radio.check(timeout=3_000)
                return
    except Exception:
        pass

    # By label text (look for <label for="id">).
    try:
        for radio in radio_group.all():
            rid = radio.get_attribute("id") or ""
            if rid:
                lbl = page.locator(f'label[for="{rid}"]')
                if lbl.count() > 0:
                    label_text = lbl.first.inner_text().strip().lower()
                    if label_text == v_lower:
                        radio.check(timeout=3_000)
                        return
    except Exception:
        pass
