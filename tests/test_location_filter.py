"""Location filter tests — 30 fixtures across US accept, non-US reject, NYC bonus."""

from __future__ import annotations

import pytest

from autoapply.select.location_filter import (
    country_from_location,
    is_us_location,
    nyc_bonus,
)


# -- US accepts (should all pass is_us_location) -----------------------------

US_ACCEPT = [
    "New York, NY",
    "San Francisco, CA",
    "Seattle, WA",
    "Boston, MA",
    "Austin, TX, USA",
    "Chicago, IL",
    "Los Angeles, CA",
    "Remote",
    "Remote - US",
    "Remote (US)",
    "Remote, United States",
    "United States",
    "College Park, MD",
    "Washington, DC",
    "Atlanta, GA",
    "Denver, CO",
    "Minneapolis, MN",
    "Mountain View, CA",
]


# -- Non-US rejects (should all fail is_us_location) -------------------------

NON_US_REJECT = [
    "London, UK",
    "Toronto, Canada",
    "Bangalore, India",
    "Berlin, Germany",
    "Remote - EMEA",
    "Remote - Canada",
    "Remote - Europe",
    "Paris, France",
    "Hyderabad, India",
    "Dublin, Ireland",
    "Tel Aviv, Israel",
    "Sydney, Australia",
]


@pytest.mark.parametrize("loc", US_ACCEPT)
def test_is_us_location_accept(loc):
    assert is_us_location(loc) is True, f"expected {loc!r} to be US-accepted"


@pytest.mark.parametrize("loc", NON_US_REJECT)
def test_is_us_location_reject(loc):
    assert is_us_location(loc) is False, f"expected {loc!r} to be rejected"


# -- country_from_location ---------------------------------------------------


def test_country_from_location_us():
    assert country_from_location("New York, NY") == "US"
    assert country_from_location("Remote - US") == "US"
    assert country_from_location("College Park, MD, USA") == "US"


def test_country_from_location_non_us():
    assert country_from_location("London, UK") == "GB"
    assert country_from_location("Toronto, Canada") == "CA"
    assert country_from_location("Bangalore, India") == "IN"
    assert country_from_location("Remote - EMEA") == "REGION"


def test_country_from_location_ambiguous_remote_is_none():
    """Bare 'Remote' with no country signal is ambiguous → None (accept)."""
    assert country_from_location("Remote") is None


# -- NYC bonus ---------------------------------------------------------------


@pytest.mark.parametrize("loc", [
    "New York, NY",
    "NYC",
    "Manhattan, NY",
    "Brooklyn, NY",
    "Jersey City, NJ",
    "New York City",
])
def test_nyc_bonus_fires(loc):
    assert nyc_bonus(loc) == 0.15


@pytest.mark.parametrize("loc", [
    "San Francisco, CA",
    "Seattle, WA",
    "Austin, TX",
    "Remote",
    "Boston, MA",
    "",
])
def test_nyc_bonus_not_fires(loc):
    assert nyc_bonus(loc) == 0.0


# -- Edge cases --------------------------------------------------------------


def test_empty_location_is_accepted():
    assert is_us_location("") is True


def test_whitespace_only_location_is_accepted():
    assert is_us_location("   ") is True
