# Submitter subpackage — Playwright-bound form filling

## Phase order (driver.py composes these)

```
browser.launch_browser_context      — Chromium + stealth + UA pool
navigate                             — page.goto + captcha check
upload.upload_files                  — resume + cover letter
upload.wait_for_resume_analysis      — Lever re-render wait
api_fill.fill_api_fields             — Stage-1 resolved data dict
lever_cards.fill_lever_cards         — Lever-only (if jobs.lever.co)
stage2.run_stage2_batch              — DOM-scrape + batch LLM for SPA fields
label_fallback.fill_by_label         — safety net for location atoms
diagnostics.dump_pre_submit_state    — DOM dump + screenshot
submit_click.click_submit_and_handle_captcha
phases.verification.handle_email_verification  — IMAP OTP (Greenhouse)
verify.check_submit_success          — final detect + error capture
```

**Do not reorder.** Resume upload MUST happen before text fill (Lever
re-renders on parse and wipes prior fills). Stage-2 DOM batch MUST
run after upload (SPA injects fields only after the resume analysis
completes).

## Key invariants

- **Backcompat shims:** `dom_batch.py` and `field_fill.py` are 41-LOC
  re-export shims. New code imports from `.dom` and `.fillers`
  respectively. External tests monkeypatch `dom_batch._call_with_cascade`
  and friends — those names MUST stay re-exported.
- **Stage-2 fires for any SPA-backed tenant**: Greenhouse SPA
  (job-boards.greenhouse.io) and Ashby (jobs.ashbyhq.com). For Lever
  it runs too but most fields are already handled by
  `fill_lever_cards`. For Ashby it does most of the work because
  Ashby exposes no form-schema API to third-party portals.
  `batch_resolve_dom_fields` returns an empty audit on non-SPA
  forms and moves on.
- **Late-rescan pass** (`dom/batch.py::_rescan_for_late_fields`)
  catches EEO fields that render only after cascading selects fill —
  Axon's Race-after-Gender is the reference case.
- **React-Select fills re-locate by id/name at fill time**, not via
  the captured locator from scrape — React-Select unmount/remount
  breaks stale locators. See `dom/fill.py::_fill_one`.

## Gotchas

- **Never call `.click()` on a `[role="option"]` without `dispatch_event('mousedown')` first.** React-Select listens to
  `onMouseDown`, not `onClick`. See `fillers/react_select.py::_commit_pick`.
- **No binary Yes/No fallback in `fill_combobox`.** Removed intentionally
  — it picked "Yes" for "Do you have GPA of 4.0+?" with a 3.975 GPA.
  If nothing matches the option list, raise and let the caller route
  to review.
- **Numeric-value-vs-non-numeric-options defers to the batch LLM.**
  `dom/resolve.py::_try_classifier_resolve` returns `None` when the
  bank produces a strictly-numeric value and none of the select
  options contain a digit (e.g. profile GPA `"3.975"` vs
  `["Yes", "No"]` for a threshold question). The batch LLM applies
  the threshold rule in `prompts/batch_rules.md`. Text-vs-text
  mismatches (profile school vs partial async-typeahead options)
  still fall through to `fill_combobox` which types-and-filters.
  Do NOT broaden this to "defer any no-match select" — that path was
  tried on 2026-04-21 and broke async-typeahead schools.
- **Multi-option checkbox groups (`name="foo[]"`) match by label/value,
  not `.first`.** `fillers/dispatch.py`'s checkbox branch iterates
  every checkbox in the group and checks only those whose `value`
  attribute or `<label for=id>` text matches the resolver's answer.
  Supports comma-separated multi-select. When nothing matches, it
  checks nothing (silent skip) — never fall back to `.first`, which
  on a 50-state grid always picked Alabama. Regression test:
  `test_fillers_package.py::test_checkbox_group_picks_matching_label_not_first`.
- **Phone country-code picker is always mounted** on Greenhouse SPA
  forms (off-screen). Always scope option-scrape by `aria-controls`
  or ancestor container — never use page-wide `[role="listbox"]`
  without `:visible`.
- **`_close_dropdown_state` (direct `document.activeElement.blur()`)
  between fills.** Tab / Escape / mouse-click-elsewhere all break
  React-Select commits differently. Only blur is safe.

## Test the submitter

- `tests/test_dom_package.py` — backcompat + preference matcher.
- `tests/test_fillers_package.py` — match ladder + declination keywords.
- `tests/test_driver_phases.py` — phase-module import contracts.
- Full suite: `/test` (~1.2s).
