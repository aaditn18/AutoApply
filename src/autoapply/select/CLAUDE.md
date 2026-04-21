# Select subpackage — job filtering + ranking

## Hard filters vs. soft signals

**Hard filters — applied BEFORE ranking. A hit = job rejected.**

- `location_filter.py::is_us_location` — **strict US-whitelist** (inverted
  2026-04-21 after Bulgaria-remote slipped through the old non-US
  denylist). Accepts only when `_US_ACCEPT_REGEX` matches: US country
  variant (`US`, `USA`, `United States`, `U.S.`, `U.S.A.`), any of 50
  state full names + DC + PR (whitespace-flexible multi-word), or any
  2-letter state abbrev — all word-bounded, matched anywhere in the
  string. Bare `"Remote"` and `"Remote, <non-US-country>"` both
  rejected. Non-US country denylists are maintained for attribution
  (`country_from_location` returns `"GB"` etc.) but `is_us_location`
  no longer treats ambiguous strings as probably-US.
- `yoe_filter.py::is_yoe_eligible` — reject when the JD requires ≥3
  years post-grad experience. Regex scans `N+ years` / `minimum N years`
  in the JD.
- `dedup.py::applied_in_last_60d(company)` — 60-day per-company cap.
  Reapplying to the same company within 60 days is blocked.

**Soft signals — bias ranking, never reject:**

- `pay_extractor.py::extract_pay_midpoint` — regex scan for salary
  ranges, normalize to annual USD midpoint. Maps to `pay_signal`
  (0.0 – 0.30). Missing pay → 0.10 (neutral — don't punish undisclosed).
- `scorer.py::location_bonus` — 0.15 bonus for NYC markers. Other
  major markets (SF, Seattle, etc.) neutral.
- `scorer.py::score_job` — `final_rank = base_fit + pay_signal + loc_signal`.

## Track picker (`track_picker.py`) invariants

- **No company-name list.** No hardcoded "this firm is quant" table
  by design. New prop shops appear constantly; a firm list goes stale
  and misses cross-industry quant (e.g., sports-betting, prediction
  markets).
- **Title rules first, description-signal promotion second.**
  Strong title keywords (`quant`, `HFT`, `trader`, `SWE`, `ML eng`, …)
  deterministically map to track. Only when the title is generic
  ("Software Engineer") does the description scan run.
- **`quant_weight >= 4` in description promotes to `quant` track.**
  Weighted keyword tallies — see `QUANT_DESC_SIGNALS` in `track_picker.py`.
- **LLM is only a last-resort tiebreaker.** When skill-overlap scores
  across tracks are within 0.15 of each other AND no title/description
  rules fired. Logged for audit.

## Gotchas

- **YOE is never LLM-answered.** Computed from resume dates at
  profile-build time (see `profile/build.py`). The classifier's
  `YOE_LANGUAGE` type looks up `profile.years_of_experience[skill]`.
- **Pay extraction uses focus windows** to avoid false positives
  (e.g., "25 years" inside a company history paragraph — not salary).
- **Dedup uses `canonical_key` (stable hash of title+company+url)**,
  not `source_id` — GH and Lever assign different IDs to the same
  role cross-posted.

## Tests

- `tests/test_location_filter.py` — 31 location strings (US variants
  accepted, non-US rejected, NYC bonus).
- `tests/test_pay_extractor.py` — 28 real JD snippets.
- `tests/test_yoe_filter.py` — 29 YOE requirement phrasings.
- `tests/test_track_picker.py` — 21 hand-labeled JDs.
- `tests/test_dedup.py` — 22 dedup scenarios.
- `tests/test_scorer.py` — 12 ranking edge cases.
