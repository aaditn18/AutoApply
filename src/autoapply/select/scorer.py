"""Compose `final_rank` from LLM fit + pay signal + location signal.

`base_fit` comes from the LLM scoring stage (Gemini Flash, separate module).
This file is just the deterministic combiner so it's easy to unit-test and
audit — every factor is stored on the job row.
"""

from __future__ import annotations

from dataclasses import dataclass

from autoapply.select.location_filter import is_us_location, nyc_bonus
from autoapply.select.pay_extractor import extract_pay, pay_signal


@dataclass
class ScoredJob:
    """Fields written onto the job row after scoring. Deterministic — the
    audit trail lets us re-score offline without re-calling the LLM."""

    base_fit: float              # 0.0–1.0 from the LLM scorer
    pay_midpoint: float | None   # annualized USD midpoint, or None
    pay_signal: float            # 0.0–0.30
    loc_signal: float            # 0.0–0.15
    final_rank: float            # base_fit + pay_signal + loc_signal
    us_eligible: bool
    reasons: list[str]           # short human-readable notes


def score_job(
    base_fit: float,
    description: str,
    location: str,
) -> ScoredJob:
    """Combine deterministic signals with an LLM fit score."""
    reasons: list[str] = []
    us = is_us_location(location)
    if not us:
        reasons.append("non-US location")
        return ScoredJob(
            base_fit=base_fit,
            pay_midpoint=None,
            pay_signal=0.0,
            loc_signal=0.0,
            final_rank=0.0,
            us_eligible=False,
            reasons=reasons,
        )

    pay = extract_pay(description)
    pay_mid = pay.midpoint if pay is not None else None
    pay_sig = pay_signal(pay_mid)
    if pay is not None:
        reasons.append(f"pay midpoint ${pay_mid:,.0f} → signal {pay_sig:.2f}")
    else:
        reasons.append(f"pay undisclosed → neutral signal {pay_sig:.2f}")

    loc_sig = nyc_bonus(location)
    if loc_sig > 0:
        reasons.append(f"NYC bonus {loc_sig:.2f}")

    return ScoredJob(
        base_fit=base_fit,
        pay_midpoint=pay_mid,
        pay_signal=pay_sig,
        loc_signal=loc_sig,
        final_rank=round(base_fit + pay_sig + loc_sig, 4),
        us_eligible=True,
        reasons=reasons,
    )
