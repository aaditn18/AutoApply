"""Tests for the execute/submitter/dom/ package.

The dom subpackage was split from a 1200-LOC dom_batch.py module. Most
of its functions are Playwright-bound and exercised via live submission
tests. This file locks down:

1. **Backwards compatibility** — the old ``dom_batch`` import paths
   still resolve, and resolve to the SAME function objects as the new
   ``dom`` package exports. If someone accidentally reintroduces the
   old monolith, the ``is``-identity check catches it.

2. **Pure-data / pure-function behavior** — the preferences module is
   standalone and deterministic; we test it directly.

3. **Orchestrator audit shape** — ``batch_resolve_dom_fields`` with
   an empty field list returns a well-formed empty audit without
   crashing. Catches basic composition regressions.
"""

from __future__ import annotations


# ─── Backwards-compat import shim ────────────────────────────────────────


def test_dom_batch_shim_reexports_same_objects():
    """The old ``dom_batch`` path must point at the same function
    objects the new ``dom`` package exports — not a duplicate copy."""
    from autoapply.execute.submitter import dom, dom_batch

    assert dom.batch_resolve_dom_fields is dom_batch.batch_resolve_dom_fields
    assert dom.collect_empty_required_fields is dom_batch.collect_empty_required_fields
    assert dom._DomField is dom_batch._DomField
    assert dom._match_preferred_option is dom_batch._match_preferred_option
    assert dom._education_patterns_for_label is dom_batch._education_patterns_for_label
    # Data values (tuples, frozenset) compare by value; identity isn't
    # guaranteed because tuple() on a list produces a new tuple each
    # time. Identity IS guaranteed for the module-level constants we
    # intern at import.
    assert dom._SCHOOL_OPTION_PREFERENCES is dom_batch._SCHOOL_OPTION_PREFERENCES
    assert dom._US_STATE_LABELS is dom_batch._US_STATE_LABELS


def test_dom_package_exposes_batch_at_top_level():
    """``from autoapply.execute.submitter.dom import batch_resolve_dom_fields``
    is the new recommended import. Lock it in."""
    from autoapply.execute.submitter.dom import batch_resolve_dom_fields
    from autoapply.execute.submitter.dom.batch import (
        batch_resolve_dom_fields as from_batch,
    )

    assert batch_resolve_dom_fields is from_batch


# ─── Preferences (pure-data module) ──────────────────────────────────────


def test_preferences_match_school_college_park_first():
    """The SCHOOL preference list's first regex picks a College Park
    variant over a Baltimore one — the regression that made us add
    the preference system in the first place."""
    from autoapply.execute.submitter.dom.preferences import (
        _SCHOOL_OPTION_PREFERENCES,
        _match_preferred_option,
    )

    options = [
        "University of Maryland - Baltimore County",
        "University of Maryland - College Park",
        "University of Maryland, Eastern Shore",
    ]
    picked = _match_preferred_option(options, _SCHOOL_OPTION_PREFERENCES)
    assert picked == "University of Maryland - College Park"


def test_preferences_match_degree_bachelor_of_science_beats_bs():
    from autoapply.execute.submitter.dom.preferences import (
        _DEGREE_OPTION_PREFERENCES,
        _match_preferred_option,
    )

    options = ["Bachelor's Degree", "B.S.", "Bachelor of Science"]
    picked = _match_preferred_option(options, _DEGREE_OPTION_PREFERENCES)
    assert picked == "Bachelor of Science"


def test_preferences_match_discipline_cs_over_computing():
    from autoapply.execute.submitter.dom.preferences import (
        _DISCIPLINE_OPTION_PREFERENCES,
        _match_preferred_option,
    )

    options = ["Computing", "Computer Science", "Software Engineering"]
    picked = _match_preferred_option(options, _DISCIPLINE_OPTION_PREFERENCES)
    assert picked == "Computer Science"


def test_preferences_match_returns_none_on_empty_inputs():
    from autoapply.execute.submitter.dom.preferences import (
        _SCHOOL_OPTION_PREFERENCES,
        _match_preferred_option,
    )

    assert _match_preferred_option([], _SCHOOL_OPTION_PREFERENCES) is None
    assert _match_preferred_option(["University of Maryland"], ()) is None


def test_education_patterns_for_label_maps_known_labels():
    from autoapply.execute.submitter.dom.preferences import (
        _DEGREE_OPTION_PREFERENCES,
        _DISCIPLINE_OPTION_PREFERENCES,
        _SCHOOL_OPTION_PREFERENCES,
        _education_patterns_for_label,
    )

    assert _education_patterns_for_label("School") is _SCHOOL_OPTION_PREFERENCES
    assert _education_patterns_for_label("University") is _SCHOOL_OPTION_PREFERENCES
    assert _education_patterns_for_label("College") is _SCHOOL_OPTION_PREFERENCES
    assert _education_patterns_for_label("Degree") is _DEGREE_OPTION_PREFERENCES
    assert _education_patterns_for_label("Major") is _DISCIPLINE_OPTION_PREFERENCES
    assert _education_patterns_for_label("Field of study") is _DISCIPLINE_OPTION_PREFERENCES
    assert _education_patterns_for_label("Concentration") is _DISCIPLINE_OPTION_PREFERENCES
    # Non-education label → None
    assert _education_patterns_for_label("Phone number") is None
    assert _education_patterns_for_label("") is None


def test_education_patterns_discipline_not_degree_for_degree_discipline():
    """'Degree discipline' (seen on some tenants) must map to the
    discipline preferences, not the degree ones — 'discipline' takes
    precedence in the label match."""
    from autoapply.execute.submitter.dom.preferences import (
        _DISCIPLINE_OPTION_PREFERENCES,
        _education_patterns_for_label,
    )

    assert (
        _education_patterns_for_label("Degree discipline")
        is _DISCIPLINE_OPTION_PREFERENCES
    )


def test_us_state_labels_contains_full_set():
    from autoapply.execute.submitter.dom.preferences import _US_STATE_LABELS

    # All lowercase (consumer compares lowercased text)
    assert all(s == s.lower() for s in _US_STATE_LABELS)
    # Spot-checks.
    assert "maryland" in _US_STATE_LABELS
    assert "district of columbia" in _US_STATE_LABELS
    assert "puerto rico" in _US_STATE_LABELS


# ─── batch_resolve_dom_fields composition smoke test ────────────────────


class _StubPage:
    """Playwright-page stub that returns no elements — empty-scrape path."""

    def locator(self, _sel):
        return self

    def all(self):
        return []


def test_batch_resolve_returns_empty_audit_when_no_fields():
    """Empty DOM → no scraping, no LLM call, well-formed audit."""
    from autoapply.execute.submitter.dom import batch_resolve_dom_fields

    audit = batch_resolve_dom_fields(
        page=_StubPage(),
        profile=None,
        answer_bank_yaml="",
        track="swe",
        already_filled_keys=set(),
    )
    assert audit["scraped_count"] == 0
    assert audit["filled_count"] == 0
    assert audit["per_field"] == []
    assert audit["model_used"] == ""
    assert audit["error"] == ""
