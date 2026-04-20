"""Pure string helpers for option matching.

Neither Playwright nor the profile nor the LLM touches anything here —
these are the simplest primitives, test them independently.
"""

from __future__ import annotations

import re


def _normalize_tokens(text: str) -> frozenset[str]:
    """Lowercase + split on non-alphanumeric + drop tiny stopwords.

    Used by the token-set matcher in :func:`..native_select.fill_select`
    and :func:`..react_select.fill_combobox`. Punctuation differences
    collapse: "University of Maryland, College Park" and "University of
    Maryland-College Park" both tokenize to
    ``{"university", "of", "maryland", "college", "park"}``.

    1-char tokens are dropped (initials / single-letter noise).
    """
    tokens = re.findall(r"[A-Za-z0-9]+", (text or "").lower())
    return frozenset(t for t in tokens if len(t) >= 2)


_PLACEHOLDER_RX = re.compile(
    r"^(select(\.{3})?(\s+(an?\s+option|one))?|choose(\.{3})?|please\s+select|--+|—+)$"
)


def _looks_like_placeholder(text: str) -> bool:
    """True if a dropdown option's text looks like a placeholder row.

    Matches: "Select...", "Select", "Choose...", "Please select", "--",
    "—", "Select an option", "Select one". Empty / whitespace-only text
    also counts as placeholder (nothing to pick).
    """
    t = (text or "").strip().lower()
    if not t:
        return True
    return bool(_PLACEHOLDER_RX.match(t))
