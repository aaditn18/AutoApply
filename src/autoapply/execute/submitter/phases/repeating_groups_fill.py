"""Deterministically fill Employment + Education repeating-group rows
from ``profile.experiences`` and ``profile.education``.

These fields have a 100% deterministic mapping to the profile
(``company-name-0`` is always ``experiences[0].company``), so they
don't need an LLM and shouldn't be sent to Stage-2 — adding 6 fields
× 5 employment rows × 2 sections to the Stage-2 batch can tip the
LLM over a token / response-size limit, causing the WHOLE batch to
fail and leaving every other field unfilled too. That regression
showed up on Lyft (4 employment rows + Stage-2 batch failed wholesale).

Phase order in driver.py:

    Phase 4 (Lever cards)
    Phase 4.5  expand_repeating_groups   — clicks "Add another"
    Phase 4.6  fill_repeating_groups     — THIS PHASE
    Phase 5    run_stage2_batch          — sees only what's left

For each profile.experience[N] (capped at MAX_ROWS) we fill:
  - company-name-N         (text)
  - title-N                (text)
  - start-date-month-N     (combobox; full month name "May")
  - start-date-year-N      (combobox; "2025")
  - end-date-month-N       (combobox; skipped when is_present)
  - end-date-year-N        (combobox; skipped when is_present)
  - current-role-N_1       (checkbox; checked when is_present)

For each profile.education[N]:
  - school--N              (combobox; "University of Maryland")
  - degree--N              (combobox; "B.S." split off the leading
                           degree token)
  - discipline--N          (combobox; the rest of the degree string)
"""

from __future__ import annotations

import calendar
import logging
import re
from typing import Any

log = logging.getLogger(__name__)

MAX_ROWS = 5

# Settle a beat after each fill so React-Select's commit cycle
# completes before the next field's locator resolves.
_FILL_SETTLE_S = 0.15


def fill_repeating_groups(page: Any, profile: Any | None) -> dict[str, int]:
    """Fill Employment + Education rows from the candidate's profile.

    Returns ``{"employment_filled": int, "education_filled": int}``.
    """
    audit = {"employment_filled": 0, "education_filled": 0}
    if profile is None:
        return audit

    experiences = list(getattr(profile, "experiences", []) or [])[:MAX_ROWS]
    education = list(getattr(profile, "education", []) or [])[:MAX_ROWS]

    for i, exp in enumerate(experiences):
        try:
            ok = _fill_employment_row(page, i, exp)
        except Exception as exc:
            log.debug("fill_employment_row %d failed: %s", i, exc)
            ok = False
        if ok:
            audit["employment_filled"] += 1

    for i, edu in enumerate(education):
        try:
            ok = _fill_education_row(page, i, edu)
        except Exception as exc:
            log.debug("fill_education_row %d failed: %s", i, exc)
            ok = False
        if ok:
            audit["education_filled"] += 1

    if audit["employment_filled"] or audit["education_filled"]:
        log.info(
            "fill_repeating_groups: employment=%d/%d education=%d/%d",
            audit["employment_filled"], len(experiences),
            audit["education_filled"], len(education),
        )
    return audit


# ── Per-row fillers ──────────────────────────────────────────────────


def _fill_employment_row(page: Any, idx: int, exp: Any) -> bool:
    """Fill one Employment row at index ``idx``. Returns True on
    apparent success."""
    company = (getattr(exp, "company", "") or "").strip()
    title = (getattr(exp, "title", "") or "").strip()
    if not company and not title:
        return False

    dr = getattr(exp, "date_range", None)
    start = getattr(dr, "start", None) if dr else None
    end = getattr(dr, "end", None) if dr else None
    is_present = bool(getattr(dr, "is_present", False)) if dr else False

    filled_any = False

    if company:
        filled_any |= _fill_text(page, f"company-name-{idx}", company)
    if title:
        filled_any |= _fill_text(page, f"title-{idx}", title)

    if start is not None:
        filled_any |= _fill_combo(
            page, f"start-date-month-{idx}", calendar.month_name[start.month],
        )
        filled_any |= _fill_combo(
            page, f"start-date-year-{idx}", str(start.year),
        )
    if end is not None and not is_present:
        filled_any |= _fill_combo(
            page, f"end-date-month-{idx}", calendar.month_name[end.month],
        )
        filled_any |= _fill_combo(
            page, f"end-date-year-{idx}", str(end.year),
        )
    if is_present:
        # The actual checkbox id is "current-role-{idx}_1" because the
        # widget is a single-checkbox group with a value attr "1".
        filled_any |= _check_checkbox(page, f"current-role-{idx}_1")

    return filled_any


def _fill_education_row(page: Any, idx: int, edu: Any) -> bool:
    """Fill one Education row. Greenhouse uses double-dash ids:
    ``school--N`` / ``degree--N`` / ``discipline--N``."""
    school = (getattr(edu, "school", "") or "").strip()
    degree_raw = (getattr(edu, "degree", "") or "").strip()
    degree, discipline = _split_degree(degree_raw)

    filled_any = False
    if school:
        filled_any |= _fill_combo(page, f"school--{idx}", school)
    if degree:
        filled_any |= _fill_combo(page, f"degree--{idx}", degree)
    if discipline:
        filled_any |= _fill_combo(page, f"discipline--{idx}", discipline)
    return filled_any


# ── Field-type fillers ──────────────────────────────────────────────


def _fill_text(page: Any, element_id: str, value: str) -> bool:
    """Plain text fill via Playwright `.fill()` — used for the
    `company-name-N` and `title-N` text inputs."""
    try:
        loc = page.locator(f'#{_css_escape(element_id)}')
    except Exception:
        loc = None
    if loc is None:
        return False
    try:
        if loc.count() == 0:
            return False
        loc.first.fill(value, timeout=3_000)
        import time
        time.sleep(_FILL_SETTLE_S)
        return True
    except Exception as exc:
        log.debug("fill_text(%s) failed: %s", element_id, exc)
        return False


def _fill_combo(page: Any, element_id: str, value: str) -> bool:
    """React-Select combobox fill — month / year / school / degree /
    discipline. Reuses the existing ``fill_combobox`` helper which
    handles the type-and-pick dance + education preference
    patterns."""
    if not value:
        return False
    try:
        loc = page.locator(f'#{_css_escape(element_id)}')
        if loc.count() == 0:
            # School/Degree/Discipline ids contain `--`. The CSS
            # escape may not preserve them across DOM-API surfaces;
            # try `[id="..."]` as a fallback.
            loc = page.locator(f'[id="{element_id}"]')
        if loc.count() == 0:
            return False
        el = loc.first
    except Exception as exc:
        log.debug("fill_combo(%s) locator failed: %s", element_id, exc)
        return False

    # School / Degree / Discipline get education-aware preferences so
    # the College Park UMD campus is preferred over Baltimore. Months
    # / years pass no prefer_patterns.
    prefer_patterns: list[Any] | None = None
    if element_id.startswith("school--") or element_id.startswith(
        "degree--"
    ) or element_id.startswith("discipline--"):
        try:
            from ..dom.preferences import _education_patterns_for_label
            label_hint = (
                "School" if element_id.startswith("school--")
                else "Degree" if element_id.startswith("degree--")
                else "Discipline"
            )
            prefer_patterns = _education_patterns_for_label(label_hint)
        except Exception:
            prefer_patterns = None

    try:
        from ..field_fill import fill_combobox
        fill_combobox(page, el, value, prefer_patterns=prefer_patterns)
        import time
        time.sleep(_FILL_SETTLE_S)
        return True
    except Exception as exc:
        log.debug("fill_combo(%s) fill_combobox failed: %s", element_id, exc)
        return False


def _check_checkbox(page: Any, element_id: str) -> bool:
    try:
        loc = page.locator(f'[id="{element_id}"]')
        if loc.count() == 0:
            return False
        loc.first.check(timeout=2_500, force=True)
        return True
    except Exception as exc:
        log.debug("check_checkbox(%s) failed: %s", element_id, exc)
        return False


# ── Helpers ──────────────────────────────────────────────────────────


_DEGREE_PREFIX_RE = re.compile(
    r"^("
    r"B\.?\s?[ASE]\.?\.?"        # B.S. / B.A. / B.E. / BS / BA
    r"|M\.?\s?[ASE]\.?\.?"       # M.S. / M.A. / etc
    r"|Ph\.?\s?D\.?\.?"
    r"|Bachelor(?:'s)?(?:\s+of\s+\w+)?"
    r"|Master(?:'s)?(?:\s+of\s+\w+)?"
    r"|Doctor(?:ate)?"
    r"|Associate'?s?"
    r")\s+",
    re.I,
)


def _split_degree(raw: str) -> tuple[str, str]:
    """Split a combined degree string like "B.S. Computer Science,
    Mathematics" into (degree, discipline) = ("B.S.", "Computer
    Science, Mathematics").

    If the prefix doesn't match a known degree token, returns
    (raw, "") — let the combobox dropdown match what it can.
    """
    m = _DEGREE_PREFIX_RE.match(raw)
    if not m:
        return (raw, "")
    degree = m.group(1).strip()
    discipline = raw[m.end():].strip()
    return (degree, discipline)


def _css_escape(s: str) -> str:
    """Escape an id for use in a CSS `#name` selector. Greenhouse ids
    contain hyphens (fine in CSS) but not other special chars; we
    mostly need this to be safe against future weirdness."""
    return s.replace('"', '\\"').replace('#', '\\#')
