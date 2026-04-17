"""Compose `final_rank` from LLM fit + pay signal + location signal + freshness.

`base_fit` comes from the LLM scoring stage (Gemini Flash, separate module).
This file is just the deterministic combiner so it's easy to unit-test and
audit — every factor is stored on the job row.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

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
    freshness_signal: float      # +0.10 (<24h) / 0.0 (normal) / -0.15 (>60d)
    final_rank: float            # base_fit + pay_signal + loc_signal + freshness_signal
    us_eligible: bool
    reasons: list[str]           # short human-readable notes


def freshness_signal(posted_at: str | None) -> float:
    """Return a ranking bonus/penalty based on how recently the job was posted.

    Tiers:
      < 24 hours  →  +0.10  (apply before the flood of applicants)
      24h – 60d   →   0.00  (normal window, neutral)
      > 60 days   →  -0.15  (likely filled or stale; still try, but deprioritise)
      unknown     →   0.00  (no `posted_at` data, don't punish)
    """
    if not posted_at:
        return 0.0
    try:
        # Handle both ISO 8601 with/without timezone and bare date strings.
        from dateutil import parser as dtparser
        dt = dtparser.parse(posted_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        age = now - dt
        if age < timedelta(hours=24):
            return 0.10
        if age > timedelta(days=60):
            return -0.15
        return 0.0
    except Exception:
        return 0.0


def score_job(
    base_fit: float,
    description: str,
    location: str,
    posted_at: str | None = None,
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
            freshness_signal=0.0,
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

    fresh_sig = freshness_signal(posted_at)
    if fresh_sig > 0:
        reasons.append(f"fresh posting (<24h) +{fresh_sig:.2f}")
    elif fresh_sig < 0:
        reasons.append(f"stale posting (>60d) {fresh_sig:.2f}")

    return ScoredJob(
        base_fit=base_fit,
        pay_midpoint=pay_mid,
        pay_signal=pay_sig,
        loc_signal=loc_sig,
        freshness_signal=fresh_sig,
        final_rank=round(base_fit + pay_sig + loc_sig + fresh_sig, 4),
        us_eligible=True,
        reasons=reasons,
    )
