"""Click "Add another" buttons to expose every employment / education
row before Stage-2 DOM batch scrapes the form.

Greenhouse's embed form (and any tenant on it) renders repeating-group
sections like Employment + Education with only the FIRST row visible
on initial load. Subsequent rows (`company-name-1`, `title-1`, etc)
appear only after the user clicks an "Add another" button.

Stage-2 DOM batch's scrape only sees fields currently in the DOM, so
without expanding these groups upfront we can only fill experience[0]
and education[0] — every other resume entry stays missing.

This phase:
  1. Finds each ``button.add-another-button`` on the page.
  2. Inspects its previous-sibling row's input ids to classify the
     section (employment vs education vs unknown).
  3. Clicks the button enough times to expose one row per
     profile entry (clamped to MAX_ROWS).
  4. Settles briefly between clicks so React can render the new row.

Idempotent on re-run: on a second pass the existing `-N` rows mean
fewer clicks are needed (we count current rows and compare to the
target).
"""

from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger(__name__)


# Cap how many entries we expand. 5 covers Aadit's resume comfortably
# (PayPal, Sociable AI, ThinkAI, UMD TA = 4) and leaves headroom.
MAX_ROWS = 5

# Settle time after each "Add another" click, so React mounts the
# new fields before we count again.
_CLICK_SETTLE_S = 0.4


def expand_repeating_groups(page: Any, profile: Any | None) -> dict[str, int]:
    """Expand employment + education sections to match the candidate's
    resume.

    Returns a small audit dict for logging:
      {"employment_rows": int, "education_rows": int}
    """
    audit = {"employment_rows": 0, "education_rows": 0}

    n_experiences = _count(profile, "experiences")
    n_education = _count(profile, "education")

    target_employment = min(n_experiences, MAX_ROWS)
    target_education = min(n_education, MAX_ROWS)

    if target_employment <= 1 and target_education <= 1:
        # Nothing to expand — defaults already cover the first row.
        log.debug(
            "expand_groups: nothing to expand "
            "(experiences=%s, education=%s)",
            n_experiences, n_education,
        )
        return audit

    # Snapshot the buttons + their kinds before clicking. After a
    # click, React unmounts/remounts so locators captured by index
    # may go stale; we re-locate by section kind on each iteration.
    try:
        buttons = page.locator("button.add-another-button").all()
    except Exception as exc:
        log.debug("expand_groups: locator failed: %s", exc)
        return audit

    if not buttons:
        return audit

    # Click loop: do up to a fixed number of passes, on each pass
    # re-locate buttons (stale-handle safe) and click any whose
    # section kind hasn't reached its target.
    for _pass in range(MAX_ROWS):
        try:
            buttons = page.locator("button.add-another-button").all()
        except Exception:
            break

        clicked_this_pass = False
        for btn in buttons:
            kind = _classify_button(page, btn)
            current = _count_rows(page, kind)
            target = (
                target_employment if kind == "employment"
                else target_education if kind == "education"
                else 0
            )
            if target <= 0 or current >= target:
                continue
            try:
                btn.scroll_into_view_if_needed(timeout=2_000)
            except Exception:
                pass
            try:
                btn.click(timeout=3_000)
            except Exception as exc:
                log.debug(
                    "expand_groups: click failed for %s: %s", kind, exc
                )
                continue
            time.sleep(_CLICK_SETTLE_S)
            clicked_this_pass = True
            if kind == "employment":
                audit["employment_rows"] = _count_rows(page, "employment")
            elif kind == "education":
                audit["education_rows"] = _count_rows(page, "education")

        if not clicked_this_pass:
            break

    # Final tallies
    audit["employment_rows"] = _count_rows(page, "employment")
    audit["education_rows"] = _count_rows(page, "education")
    log.info(
        "expand_groups: rows now employment=%s education=%s "
        "(targets %s / %s)",
        audit["employment_rows"], audit["education_rows"],
        target_employment, target_education,
    )
    return audit


# ── Helpers ──────────────────────────────────────────────────────────


def _count(profile: Any | None, attr: str) -> int:
    if profile is None:
        return 0
    try:
        seq = getattr(profile, attr, None) or []
        return len(seq)
    except Exception:
        return 0


def _count_rows(page: Any, kind: str) -> int:
    """Count visible repeating-group rows of a given kind.

    Employment rows are marked by ``input[id^="company-name-"]``;
    education rows by ``input[id^="school--"]``.
    """
    selector = (
        'input[id^="company-name-"]' if kind == "employment"
        else 'input[id^="school--"]' if kind == "education"
        else None
    )
    if not selector:
        return 0
    try:
        return page.locator(selector).count()
    except Exception:
        return 0


def _classify_button(page: Any, btn: Any) -> str:
    """Return 'employment' / 'education' / 'unknown' based on the
    button's preceding sibling input ids."""
    try:
        ids = btn.evaluate(
            """(el) => {
                let prev = el.previousElementSibling;
                for (let i = 0; i < 5 && prev; i++) {
                    const f = prev.querySelectorAll('input,select,textarea');
                    if (f.length) {
                        return Array.from(f).map(x => x.id || '').filter(Boolean);
                    }
                    prev = prev.previousElementSibling;
                }
                return [];
            }"""
        )
    except Exception:
        return "unknown"

    if not isinstance(ids, list):
        return "unknown"
    for i in ids:
        if isinstance(i, str):
            if i.startswith("company-name-"):
                return "employment"
            if i.startswith("school--"):
                return "education"
    return "unknown"
