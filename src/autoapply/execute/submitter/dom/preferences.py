"""Education-dropdown option preferences + US-state labels.

Pure data + pure functions. No Playwright, no LLM — importable and
testable independently of everything else under :mod:`.dom`.

Rule data is loaded from ``state/rules/education_preferences.yml`` and
``state/rules/geography.yml`` at module import. See those files'
headers for semantics and update-policy.
"""

from __future__ import annotations

import re

from autoapply.rules import load_rules


# Ordered preference regexes for school / degree / discipline dropdowns.
# Earlier entries win. Loaded once at import.
_EDU_PREFS = load_rules("education_preferences")
_SCHOOL_OPTION_PREFERENCES: tuple[str, ...] = tuple(_EDU_PREFS["school"])
_DEGREE_OPTION_PREFERENCES: tuple[str, ...] = tuple(_EDU_PREFS["degree"])
_DISCIPLINE_OPTION_PREFERENCES: tuple[str, ...] = tuple(_EDU_PREFS["discipline"])

# US states + DC + territories, lowercase. Used by the checkbox
# pre-resolve to auto-check Maryland (or whichever states are in the
# profile's ``willing_to_work_states``) on multi-state grids.
_US_STATE_LABELS: frozenset[str] = frozenset(
    load_rules("geography")["us_state_labels"]
)


def _match_preferred_option(
    options: list[str], preferences: tuple[str, ...]
) -> str | None:
    """Return the first option text matching the highest-priority regex.

    Iterates the preference regexes in order; for each, scans the option
    list for a match (case-insensitive). Returns the exact option text
    so the caller can pass it verbatim to ``fill_combobox`` /
    ``fill_select``.
    """
    if not options or not preferences:
        return None
    for pat in preferences:
        rx = re.compile(pat, re.IGNORECASE)
        for opt in options:
            if rx.search(opt or ""):
                return opt
    return None


def _education_patterns_for_label(label: str) -> tuple[str, ...] | None:
    """Map a scraped DOM label to preferred-option regexes for education fields.

    Returns the ordered pattern tuple (``fill_combobox`` tries them in
    order; first option matching highest-priority pattern wins), or None
    if the label isn't an education field.

    Used to disambiguate multi-option matches when the typed value
    (e.g. "University of Maryland") matches many options. Without
    preferences, fill_combobox picks alphabetically-first ("Baltimore").
    With SCHOOL preferences, it picks "College Park" (Aadit's actual
    campus) every time.
    """
    lo = (label or "").strip().lower()
    if "school" in lo or "university" in lo or "college" in lo:
        return _SCHOOL_OPTION_PREFERENCES
    if "degree" in lo and "discipline" not in lo:
        return _DEGREE_OPTION_PREFERENCES
    if (
        "discipline" in lo
        or "major" in lo
        or "field of study" in lo
        or "concentration" in lo
    ):
        return _DISCIPLINE_OPTION_PREFERENCES
    return None
