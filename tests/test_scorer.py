"""Scorer tests — combiner for base_fit + pay_signal + loc_signal → final_rank."""

from __future__ import annotations

import pytest

from autoapply.select.scorer import score_job


def test_non_us_zeroes_final_rank():
    s = score_job(
        base_fit=0.8,
        description="$200k-$250k per year. Amazing role.",
        location="London, UK",
    )
    assert not s.us_eligible
    assert s.final_rank == 0.0


def test_us_posting_combines_signals():
    s = score_job(
        base_fit=0.6,
        description="The salary range for this role is $180,000 - $220,000 per year.",
        location="New York, NY",
    )
    assert s.us_eligible
    # base=0.6, pay_mid=200k → pay_signal=0.30, NYC → 0.15; total = 1.05
    assert s.final_rank == pytest.approx(1.05, abs=1e-6)
    assert s.pay_midpoint == 200_000
    assert s.loc_signal == 0.15


def test_undisclosed_pay_is_neutral():
    s = score_job(
        base_fit=0.5,
        description="Great role, apply now!",
        location="Seattle, WA",
    )
    # pay signal neutral = 0.10, no NYC bonus
    assert s.pay_midpoint is None
    assert s.pay_signal == 0.10
    assert s.loc_signal == 0.0
    assert s.final_rank == pytest.approx(0.60, abs=1e-6)


def test_low_pay_zero_signal():
    s = score_job(
        base_fit=0.7,
        description="Salary: $80,000 per year.",
        location="Austin, TX",
    )
    assert s.pay_signal == 0.0
