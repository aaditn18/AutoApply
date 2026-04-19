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
                # Detect React-Select / combobox pattern (role="combobox").
                # These need fill() + option click, not just fill().
                role = el.get_attribute("role") or ""
                if role == "combobox":
                    fill_combobox(page, el, value)
                else:
                    el.fill(value, timeout=5_000)
                return
        except Exception:
            continue

    # Nothing found — will be recorded as a fill error by the caller.
    raise ValueError(f"no element found for name/id={name!r}")


def fill_combobox(page: Any, input_el: Any, value: str) -> None:
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
    3. Wait briefly for options to populate.
    4. Click the option whose text best matches the value; fall back to
       pressing ArrowDown + Enter if no option text matches.
    """
    try:
        input_el.click(timeout=3_000)
        jitter(0.1, 0.3)

        # Clear existing content then type the value so filtering fires.
        input_el.fill("", timeout=3_000)
        input_el.type(value, delay=40)
        jitter(0.4, 0.8)  # let the dropdown filter

        # Scope to THIS combobox's listbox via aria-controls.
        # aria-controls is set dynamically after the combobox opens; read it
        # now (after the click + type above).
        # Use attribute selector [id="…"] instead of #id because some listbox
        # IDs contain CSS-special characters like `[]`.
        aria_controls = input_el.get_attribute("aria-controls") or ""
        if aria_controls:
            options = page.locator(f'[id="{aria_controls}"] [role="option"]')
        else:
            # Fallback: options inside a visible listbox only.
            options = page.locator("[role='listbox'] [role='option']")

        jitter(0.1, 0.3)
        count = options.count()

        if count == 0:
            # Nothing in dropdown — press Enter to accept whatever is typed.
            input_el.press("Enter")
            return

        v_lower = value.strip().lower()

        # Prefer the option whose text exactly equals the target value.
        for i in range(min(count, len(value) + 30)):
            opt = options.nth(i)
            try:
                txt = opt.inner_text().strip().lower()
                if txt == v_lower:
                    opt.click(timeout=3_000)
                    return
            except Exception:
                continue

        # Exact match failed — try starts-with or contains.
        for i in range(min(count, 50)):
            opt = options.nth(i)
            try:
                txt = opt.inner_text().strip().lower()
                if txt.startswith(v_lower) or v_lower in txt:
                    opt.click(timeout=3_000)
                    return
            except Exception:
                continue

        # Still nothing — click the first visible option.
        options.first.click(timeout=3_000)

    except Exception as exc:
        log.debug("combobox fill failed for value=%r: %s", value, exc)
        raise


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
    _DECLINE_KEYWORDS = ("decline", "prefer not", "not wish", "not want",
                         "not identify", "not disclose", "choose not")
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
