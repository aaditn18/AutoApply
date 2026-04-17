"""Deduplication + hard-filter gate.

Responsibilities:
  1. Compute a canonical `job_key` that survives URL noise so the same
     posting from GH + Simplify + Lever collapses to a single row.
  2. Hard-filter gate: is_us_location, 60-day per-company cap,
     injection-detected flag.

This module is pure — all state reads/writes happen in the tracker layer.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, timedelta


# -- Canonical job key -------------------------------------------------------


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(s: str) -> str:
    return _SLUG_RE.sub("-", s.lower()).strip("-")


def canonical_key(company: str, title: str, location: str = "") -> str:
    """Stable SHA-256 prefix of a (company, title, location) triple.

    - `company` is slugified (whitespace, punctuation, casing are ignored).
    - `title` is slugified and its common suffixes ("I", "II", "(Remote)")
      are stripped so rotating openings stay collapsed.
    - `location` is included but coarsely — only the first comma-chunk.
    """
    c = _slug(company)
    t = _slug(_strip_title_noise(title))
    l = _slug(location.split(",")[0] if location else "")
    raw = f"{c}|{t}|{l}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


_TITLE_SUFFIX_NOISE = (
    r"\s*\((?:remote|hybrid|on-?site|contract|full[- ]time|part[- ]time)\)\s*",
    r"\s*\|\s*(?:remote|hybrid|on-?site).*$",
    r"\s*-\s*(?:remote|hybrid|on-?site).*$",
    r"\s+\b(?:i|ii|iii|iv|new\s+grad|intern(?:ship)?)\b.*$",
)

# Seniority words that may appear as a prefix ("Senior Software Engineer") or
# just before the base title ("Staff Software Engineer"). We strip them so
# parallel postings of the same role collapse to one canonical key.
_SENIORITY_PREFIX = re.compile(
    r"^\s*(?:senior|sr\.?|jr\.?|junior|lead|staff|principal|entry[\s-]*level|new\s+grad)\s+",
    re.IGNORECASE,
)


def _strip_title_noise(title: str) -> str:
    t = title.strip()
    for pat in _TITLE_SUFFIX_NOISE:
        t = re.sub(pat, "", t, flags=re.IGNORECASE)
    # Strip seniority prefixes recursively (handles "Senior Staff Engineer").
    while True:
        new = _SENIORITY_PREFIX.sub("", t)
        if new == t:
            break
        t = new
    return t.strip()


# -- Hard-filter gate --------------------------------------------------------


@dataclass
class FilterResult:
    accepted: bool
    reason: str              # "" on acceptance; short code otherwise


def filter_hard(
    *,
    is_us: bool,
    injection_detected: bool,
    company: str,
    recent_company_applications: dict[str, date] | None = None,
    today: date | None = None,
) -> FilterResult:
    """Apply the 3 hard filters in order. First failure wins."""
    if not is_us:
        return FilterResult(False, "rejected_by_location")
    if injection_detected:
        return FilterResult(False, "rejected_by_injection")
    if recent_company_applications:
        last = recent_company_applications.get(company.lower().strip())
        if last is not None:
            today = today or date.today()
            if (today - last) < timedelta(days=60):
                return FilterResult(False, "rejected_by_company_cap")
    return FilterResult(True, "")
