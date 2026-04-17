"""Pay-extractor tests."""

from __future__ import annotations

import pytest

from autoapply.select.pay_extractor import extract_pay, pay_signal


# -- Standard range patterns -------------------------------------------------


def test_range_dollars_with_commas():
    info = extract_pay("The salary range for this role is $120,000 - $160,000 per year.")
    assert info is not None
    assert info.low == 120_000
    assert info.high == 160_000
    assert info.midpoint == 140_000
    assert info.unit == "year"


def test_range_with_k_suffix():
    info = extract_pay("Compensation: $130k-$180k annually.")
    assert info is not None
    assert info.low == 130_000
    assert info.high == 180_000
    assert info.midpoint == 155_000


def test_range_with_em_dash():
    info = extract_pay("Base salary range is $150,000 — $200,000.")
    assert info is not None
    assert info.midpoint == 175_000


def test_hourly_range_annualized():
    info = extract_pay("Hourly rate: $50 - $70 per hour.")
    assert info is not None
    assert info.unit == "hour"
    assert info.low == 50 * 2080
    assert info.high == 70 * 2080


def test_between_word_range():
    info = extract_pay(
        "The total compensation for this role is between $180,000 and $220,000 per year."
    )
    assert info is not None
    assert info.midpoint == 200_000


# -- Single value with explicit unit ----------------------------------------


def test_single_with_explicit_year():
    info = extract_pay("Salary: $175,000 per year.")
    assert info is not None
    assert info.midpoint == 175_000


def test_single_with_hourly_annualized():
    info = extract_pay("Pay range: $60 per hour.")
    assert info is not None
    assert info.unit == "hour"
    assert info.midpoint == 60 * 2080


# -- Rejections / False-positive avoidance ----------------------------------


def test_no_salary_keyword_single_skipped():
    """Bare `$120,000` with no keyword is too risky to map to comp."""
    info = extract_pay("Saved $1.5B in fees for PayPal.")
    assert info is None


def test_million_dollar_not_salary():
    info = extract_pay("Our company raised $250 million in Series C.")
    assert info is None


def test_empty_input():
    assert extract_pay("") is None
    assert extract_pay(None) is None  # type: ignore[arg-type]


# -- pay_signal tiers --------------------------------------------------------


@pytest.mark.parametrize("midpoint, expected", [
    (250_000, 0.30),
    (200_000, 0.30),
    (180_000, 0.22),
    (160_000, 0.22),
    (150_000, 0.14),
    (130_000, 0.14),
    (120_000, 0.08),
    (100_000, 0.08),
    (80_000, 0.0),
    (None, 0.10),
])
def test_pay_signal_tiers(midpoint, expected):
    assert pay_signal(midpoint) == expected


# -- Real-JD style snippets --------------------------------------------------


def test_complex_sentence_with_salary():
    info = extract_pay(
        "The compensation range for this role is $140,000 to $185,000 per year. "
        "Final offer depends on experience."
    )
    assert info is not None
    assert info.midpoint == pytest.approx(162_500, rel=1e-3)


def test_range_with_no_dollar_on_high():
    """Greenhouse sometimes formats as `$120,000 - 160,000`."""
    info = extract_pay("Salary range: $120,000 - 160,000.")
    assert info is not None
    assert info.midpoint == 140_000
