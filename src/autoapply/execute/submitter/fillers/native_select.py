"""Native ``<select>`` filling with a match waterfall.

Native selects are the simple case: options are declarative
``<option>`` children readable without interaction, and Playwright's
``select_option(label=...)`` / ``select_option(value=...)`` do most
of the work. We layer a punctuation-tolerant fallback on top so that
the profile's "University of Maryland, College Park" matches a
tenant's "University of Maryland-College Park" option.

Match waterfall:

  1. Exact ``label=`` match.
  2. Exact ``value=`` attribute match.
  3. Case-insensitive prefix match on option text.
  4. Bidirectional substring match.
  5. Normalized token-set match — punctuation-tolerant.
  6. EEO decline / "none" semantic fallback
     (loaded from ``state/rules/eeo_semantics.yml``).
  7. LLM option picker — hands the question + options + profile to
     Gemini and picks by index. Placeholder rows are excluded so the
     LLM can't pick "Select...".
"""

from __future__ import annotations

import logging
from typing import Any

from autoapply.rules import load_rules

from .detect import _read_input_label
from .matching import _looks_like_placeholder, _normalize_tokens


log = logging.getLogger(__name__)


# EEO decline synonyms — same list react_select uses; loaded from
# state/rules/eeo_semantics.yml at import.
_DECLINE_KEYWORDS: tuple[str, ...] = tuple(
    load_rules("eeo_semantics")["decline_keywords"]
)
# Additional "no / none" markers for clearance / veteran questions.
# Kept here because they're specific to the native-select semantic
# fallback — React-Select's fallback is strictly EEO-decline oriented.
_NO_KEYWORDS: tuple[str, ...] = (
    "no clearance", "none", "no polygraph", "not a protected",
)


def fill_select(sel_el: Any, value: str) -> None:
    """Try several strategies to pick the right ``<select>`` option."""
    v_lower = value.strip().lower()

    # 1. Exact label match.
    try:
        sel_el.select_option(label=value, timeout=3_000)
        return
    except Exception:
        pass

    # 2. Exact value= attribute match.
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

    # 3. Case-insensitive prefix match.
    for txt in opt_texts:
        if txt.lower().startswith(v_lower):
            try:
                sel_el.select_option(label=txt, timeout=3_000)
                return
            except Exception:
                pass

    # 4. Bidirectional substring match.
    for txt in opt_texts:
        tl = txt.lower()
        if v_lower in tl or tl in v_lower:
            try:
                sel_el.select_option(label=txt, timeout=3_000)
                return
            except Exception:
                pass

    # 5. Normalized token-set match — handles punctuation differences
    # ("University of Maryland, College Park" vs "University of
    # Maryland-College Park"), stray whitespace, and parenthesized
    # qualifiers ("New York (NY)" vs "New York"). Skip trivially short
    # values (<2 tokens) to avoid bogus matches like "Yes" matching
    # "I am yes-sure".
    v_tokens = _normalize_tokens(value)
    if len(v_tokens) >= 2:
        # First pass: exact set match (strongest signal, punctuation-only diffs).
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
                # Option is a shorter form of our value — accept only
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

    # 6. EEO decline / "no / none" semantic fallback.
    if any(kw in v_lower for kw in _DECLINE_KEYWORDS + _NO_KEYWORDS):
        for txt in opt_texts:
            tl = txt.lower()
            if any(kw in tl for kw in _DECLINE_KEYWORDS + _NO_KEYWORDS):
                try:
                    sel_el.select_option(label=txt, timeout=3_000)
                    return
                except Exception:
                    pass

    # 7. LLM option picker — last resort. Hand the question + options +
    # profile to Gemini and pick by index. Placeholder rows are excluded
    # so the model can't pick "Select...".
    _try_llm_fallback(sel_el, value, opt_texts)


def _try_llm_fallback(sel_el: Any, value: str, opt_texts: list[str]) -> None:
    """Hand the select to ``pick_option_via_llm`` as a last resort.

    Called only after every deterministic matcher has missed. The LLM
    returns an index into the placeholder-free option list; we translate
    back to the ``select_option(label=...)`` call.
    """
    try:
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

        # Skip placeholders (typically index 0) so the model can't pick
        # "Select..." as the best non-answer.
        real_opts: list[str] = []
        for txt in opt_texts:
            if txt and not _looks_like_placeholder(txt):
                real_opts.append(txt)
        if len(real_opts) < 2:
            return

        llm_idx = pick_option_via_llm(
            question=question_label, options=real_opts
        )
        if llm_idx is None:
            return
        chosen_txt = real_opts[llm_idx]
        try:
            sel_el.select_option(label=chosen_txt, timeout=3_000)
            log.info(
                "fill_select: LLM fallback picked %r for Q=%r",
                chosen_txt[:60], question_label[:80],
            )
        except Exception:
            pass
    except Exception as exc:
        log.debug("fill_select: LLM fallback failed: %s", exc)
