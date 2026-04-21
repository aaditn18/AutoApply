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
    # Flexible word-bounded match: any US token anywhere in the string.
    # State abbreviation not comma-prefixed.
    "Remote MD",
    "MD Remote",
    "MD",
    # Full state name anywhere.
    "Maryland",
    "Maryland Remote",
    "Remote Maryland",
    # Country variants.
    "USA",
    "U.S.",
    "U.S.A.",
    # Multi-word state names with flexible whitespace.
    "New York",
    "North Carolina",
    "District of Columbia",
    "Puerto Rico",
]


# -- Non-US rejects (should all fail is_us_location) -------------------------
#
# Strict-whitelist policy: anything lacking a positive US signal is
# rejected, including bare "Remote" and country names not in the
# non-US marker list. The regression fixtures at the end of this list
# (Bulgaria, Greece, etc.) are the reason we inverted the filter —
# they used to leak through because the non-US marker list can never
# be exhaustive.

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
    # Bare "Remote" with no country signal — strict whitelist rejects.
    "Remote",
    # Regression: Bulgaria was missing from the non-US marker list
    # (2026-04-20, job 990 Smartsheet). With strict whitelist, it's
    # rejected regardless.
    "-REMOTE, BULGARIA-",
    "Remote, Bulgaria",
    "Sofia, Bulgaria",
    # Other EU countries not in the marker list — all rejected now.
    "Athens, Greece",
    "Zagreb, Croatia",
    "Belgrade, Serbia",
    "Ljubljana, Slovenia",
    "Vilnius, Lithuania",
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
