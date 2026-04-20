"""Machine-name → Profile attribute mapping.

Greenhouse and Lever name their fields with stable identifiers
(``first_name``, ``email``, ``urls[linkedin]``, ``custom_fields[...]``).
For the identifiers we recognize, we skip the classifier entirely and
read the value directly from the :class:`Profile`.

The regex rule table is loaded once at import from
``state/rules/machine_keys.yml``. Order matters — first hit wins. See
the rule file's header for the ordering constraint around disability-
signature vs disability-signature-date.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from autoapply.rules import load_rules


if TYPE_CHECKING:
    from autoapply.profile.schema import Profile


# Compiled once (case-insensitive) at import.
_MACHINE_KEY_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(_row["pattern"], re.I), _row["attr"])
    for _row in load_rules("machine_keys")["rules"]
]


def _match_machine_key(name: str) -> str | None:
    """Return the ``attr`` name for the first matching rule, else None."""
    for pat, attr in _MACHINE_KEY_RULES:
        if pat.search(name or ""):
            return attr
    return None


def _profile_value(attr: str, profile: "Profile") -> str | None:
    """Read ``attr`` from ``profile``. Returns None for unknown attrs.

    ``full_name`` splits on the first whitespace (so "Aadit Nilay" →
    first="Aadit", last="Nilay"); ``today_date`` returns the current
    date in MM/DD/YYYY for Lever's EEO signature-date field.

    Unknown attrs (``website_url``, ``location``) intentionally return
    None so the caller falls through to the classifier path.
    """
    if attr == "full_name":
        return profile.full_name or None
    if attr == "first_name":
        parts = (profile.full_name or "").split(None, 1)
        return parts[0] if parts else None
    if attr == "last_name":
        parts = (profile.full_name or "").split(None, 1)
        return parts[1] if len(parts) > 1 else None
    if attr == "email":
        return profile.email or None
    if attr == "phone":
        return profile.phone or None
    if attr == "linkedin_url":
        return profile.linkedin_url or None
    if attr == "github_url":
        return profile.github_url or None
    if attr == "today_date":
        from datetime import date as _date

        return _date.today().strftime("%m/%d/%Y")
    # website_url / location aren't on Profile → fall back to classifier path.
    return None
