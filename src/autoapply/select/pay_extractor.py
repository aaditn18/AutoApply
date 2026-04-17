"""Regex pay extraction → annualized USD midpoint → `pay_signal` in [0.0, 0.30].

Many US postings now list pay due to NYC / CA / CO / WA transparency laws.
This module scans raw JD text for a dollar range, canonicalizes to USD/year,
and maps the midpoint to a ranking bonus. Deterministic; no LLM.

Contract:
  extract_pay(raw) -> PayInfo | None    # best-effort extraction
  pay_signal(pay_midpoint) -> float     # 0.0..0.30; 0.10 when None (neutral)
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# -- Public data -------------------------------------------------------------


@dataclass(frozen=True)
class PayInfo:
    low: float                # annualized USD, low end of range
    high: float               # annualized USD, high end of range
    midpoint: float           # annualized USD, midpoint
    unit: str                 # "year" or "hour" (original unit before annualization)
    source_span: str          # the raw substring we matched — for audit


# Hourly → annual conversion factor (2080 work-hours per year, standard US).
_HOURLY_TO_ANNUAL = 2080


# -- Regex --------------------------------------------------------------------
# Matches things like:
#   "$120,000 - $160,000", "$120k-$160k", "$50/hour - $65/hr",
#   "USD 130,000 — 170,000", "between $120,000 and $150,000",
#   "$180,000", "120-160k"

# A single dollar amount. Three alternatives (commas, k-suffix, bare).
# No named groups — we parse the raw matched string with `_parse_amount`.
_AMOUNT_ALT = (
    r"(?:"
        r"\$?\s*\d{1,3}(?:,\d{3})+(?:\.\d+)?"    # $120,000 / 1,200,000
        r"|\$?\s*\d{1,4}(?:\.\d+)?\s*[kK]"        # 120k / 120.5K
        r"|\$\s*\d{1,4}(?:\.\d+)?"                # bare $120 (require $ to cut false positives)
    r")"
)

_SEP = r"\s*(?:-|–|—|to|and|\bto\b)\s*"
_UNIT_RE = r"(?:\s*(?:/|\s+per\s+|\s+an?\s+|\s+a\s+)\s*(?:hour|hr|yr|year|annum))"

_RANGE_RE = re.compile(
    rf"(?P<low>{_AMOUNT_ALT}){_SEP}(?P<high>{_AMOUNT_ALT})(?P<unit>{_UNIT_RE})?",
    re.IGNORECASE,
)

_SINGLE_RE = re.compile(
    rf"(?P<single>{_AMOUNT_ALT})(?P<unit>{_UNIT_RE})",
    re.IGNORECASE,
)


def _parse_amount(raw: str) -> float | None:
    """Parse '$120,000' / '120k' / '$75' → float dollars.

    Does NOT annualize — just returns what the string explicitly says.
    """
    if raw is None:
        return None
    s = raw.strip().lstrip("$").strip()
    if not s:
        return None
    is_k = s[-1] in "kK"
    if is_k:
        s = s[:-1].strip()
    s = s.replace(",", "")
    try:
        n = float(s)
    except ValueError:
        return None
    if is_k:
        return n * 1_000
    return n


def _annualize(value: float, unit_span: str | None) -> tuple[float, str]:
    """Return (annualized_value, detected_unit).

    `unit_span` is the raw regex capture like `/hour` or `per year` (or None).
    """
    u = (unit_span or "").lower()
    if "hour" in u or "/hr" in u or " hr" in u:
        return value * _HOURLY_TO_ANNUAL, "hour"
    if u and any(k in u for k in ("year", "yr", "annum")):
        return value, "year"
    # No explicit unit. Upscale tiny values (clearly hourly).
    if value < 20_000:
        return value * _HOURLY_TO_ANNUAL, "hour"
    return value, "year"


# -- Public API --------------------------------------------------------------


def extract_pay(raw: str) -> PayInfo | None:
    """Best-effort pay extraction. Returns `None` when nothing plausible is found."""
    if not raw:
        return None

    # Restrict to a window around a $ or "salary" keyword to reduce false positives.
    # Many JDs mention dollar amounts unrelated to comp ("$1.5B in fees saved").
    haystack = _focus_window(raw)

    # Prefer ranges over singles.
    for m in _RANGE_RE.finditer(haystack):
        low = _parse_amount(m.group("low"))
        high = _parse_amount(m.group("high"))
        if low is None or high is None:
            continue
        # If low has no $ and high does (or vice versa), that's fine — the
        # regex accepts both forms. Annualize using explicit unit first.
        unit_span = m.group("unit")
        low_a, detected_unit = _annualize(low, unit_span)
        high_a, _ = _annualize(high, unit_span)
        if low_a > high_a:
            low_a, high_a = high_a, low_a
        # Reject absurd ranges (e.g. "$1.5B in fees" matched as a number)
        if high_a > 2_000_000 or low_a < 30_000:
            continue
        midpoint = (low_a + high_a) / 2
        return PayInfo(
            low=low_a,
            high=high_a,
            midpoint=midpoint,
            unit=detected_unit,
            source_span=m.group(0),
        )

    # Fallback: single amount with an explicit per-year/hour tag.
    for m in _SINGLE_RE.finditer(haystack):
        val = _parse_amount(m.group("single"))
        if val is None:
            continue
        val_a, detected_unit = _annualize(val, m.group("unit"))
        if val_a < 30_000 or val_a > 2_000_000:
            continue
        return PayInfo(
            low=val_a,
            high=val_a,
            midpoint=val_a,
            unit=detected_unit,
            source_span=m.group(0),
        )

    return None


def pay_signal(pay_midpoint: float | None) -> float:
    """Map annualized midpoint → ranking bonus in [0.0, 0.30].

    The tiers below are conservative on purpose — we don't want a $250k role
    that's a bad fit to outrank a $150k role that's a perfect fit. See the
    design doc § Selection / ranking.
    """
    if pay_midpoint is None:
        return 0.10  # neutral — unknown pay should not be punished
    if pay_midpoint >= 200_000:
        return 0.30
    if pay_midpoint >= 160_000:
        return 0.22
    if pay_midpoint >= 130_000:
        return 0.14
    if pay_midpoint >= 100_000:
        return 0.08
    return 0.0


# -- Helpers -----------------------------------------------------------------


def _focus_window(raw: str) -> str:
    """Restrict scanning to windows that actually look like comp statements.

    Pulls substrings around keywords ("salary", "compensation", "base pay",
    "range", "$") to cut false positives on things like `$1.5B transactions`.
    """
    low = raw.lower()
    anchors: list[int] = []
    for kw in ("salary", "compensation", "base pay", "pay range", "total compensation",
               "comp range", "annual salary", "per year", "per hour"):
        idx = 0
        while True:
            j = low.find(kw, idx)
            if j == -1:
                break
            anchors.append(j)
            idx = j + 1
    if not anchors:
        # No keyword — scan the whole doc, extraction will still reject
        # ranges outside the sanity window.
        return raw
    windows: list[str] = []
    for a in anchors:
        start = max(0, a - 40)
        end = min(len(raw), a + 200)
        windows.append(raw[start:end])
    return "\n".join(windows)
