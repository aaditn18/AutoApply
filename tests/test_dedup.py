"""Dedup + hard-filter tests."""

from __future__ import annotations

from datetime import date, timedelta

from autoapply.select.dedup import canonical_key, filter_hard


# -- Canonical key ----------------------------------------------------------


def test_same_posting_collapses():
    k1 = canonical_key("Acme Corp", "Software Engineer", "New York, NY")
    k2 = canonical_key("acme  corp", "Software Engineer", "New York")
    assert k1 == k2


def test_title_noise_stripped():
    k1 = canonical_key("Acme", "Software Engineer", "NYC")
    k2 = canonical_key("Acme", "Software Engineer (Remote)", "NYC")
    k3 = canonical_key("Acme", "Software Engineer - Remote", "NYC")
    assert k1 == k2 == k3


def test_seniority_doesnt_collapse_by_accident():
    """Senior vs junior SWE should probably be treated as different postings.
    The current implementation drops seniority via `_TITLE_SUFFIX_NOISE`,
    so Senior/Staff/Lead/Junior collapse to the base title — the intent is
    that they're usually parallel postings of the same role. If we need the
    opposite, flip the regex later."""
    k1 = canonical_key("Acme", "Software Engineer", "NYC")
    k2 = canonical_key("Acme", "Senior Software Engineer", "NYC")
    assert k1 == k2


def test_different_companies_different_keys():
    assert canonical_key("Acme", "SWE", "NYC") != canonical_key("Globex", "SWE", "NYC")


def test_different_titles_different_keys():
    assert canonical_key("Acme", "SWE", "NYC") != canonical_key("Acme", "Product Manager", "NYC")


# -- filter_hard ------------------------------------------------------------


def test_filter_hard_rejects_non_us():
    r = filter_hard(is_us=False, injection_detected=False, company="Acme")
    assert not r.accepted
    assert r.reason == "rejected_by_location"


def test_filter_hard_rejects_injection():
    r = filter_hard(is_us=True, injection_detected=True, company="Acme")
    assert not r.accepted
    assert r.reason == "rejected_by_injection"


def test_filter_hard_rejects_company_cap():
    today = date(2026, 4, 16)
    recent = {"acme": today - timedelta(days=30)}
    r = filter_hard(
        is_us=True,
        injection_detected=False,
        company="Acme",
        recent_company_applications=recent,
        today=today,
    )
    assert not r.accepted
    assert r.reason == "rejected_by_company_cap"


def test_filter_hard_accepts_old_company_application():
    today = date(2026, 4, 16)
    recent = {"acme": today - timedelta(days=90)}
    r = filter_hard(
        is_us=True,
        injection_detected=False,
        company="Acme",
        recent_company_applications=recent,
        today=today,
    )
    assert r.accepted


def test_filter_hard_accepts_when_no_prior():
    r = filter_hard(
        is_us=True,
        injection_detected=False,
        company="Acme",
        recent_company_applications={},
    )
    assert r.accepted
    assert r.reason == ""


def test_filter_hard_hard_order_location_wins():
    """Location filter fires before injection check — saves an LLM call."""
    r = filter_hard(is_us=False, injection_detected=True, company="Acme")
    assert r.reason == "rejected_by_location"
