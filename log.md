# AutoApply — Implementation Log

Chronological record of work. For architecture, setup, and usage docs, see
[`README.md`](./README.md). The corresponding target is
[`.claude/plans/drifting-snuggling-harbor.md`](./.claude/plans/drifting-snuggling-harbor.md).

**Current test tally: 626 passing.**

---

## 2026-04-16 — Phase 1 foundations (day 1)

Built the bottom of the stack end-to-end. All modules land with tests.

- **Bootstrap** — repo scaffolding (`pyproject.toml`, `.gitmodules` pinning
  the private resume repo as `resumes/` submodule, `.env.example`),
  `src/autoapply/` package tree, Pydantic `Settings` singleton with
  env-driven config.
- **Security** — `injection_guard.py` with 12 injection kinds, three-layer
  defense (scan → sanitize → validate_output). **109 tests** including a
  30-fixture attack corpus with 100% detection and 5 clean-JD false-positive
  negatives. Zero canary leaks in output on all 30 attacks.
- **Profile** — Pydantic `Profile` schema, Jake-Gutierrez `.tex` parser
  (brace-balanced arg extraction, date-range parsing, union-of-intervals
  YOE math), `build.py` orchestrator writing `state/profile.json` with all
  4 tracks. **22 tests.**
- **Answers** — `QuestionType` enum (50+ values) with `PROFILE_SOURCED` /
  `LLM_REQUIRED` / `REVIEW_REQUIRED` policy frozensets; rule-table
  classifier (~50 regexes, specific-first); `AnswerBank` with per-track
  override + `_default` fallback; `state/answer_bank.yml` seeded with
  ~25 entries. **75 tests.**
- **Selection** — `location_filter` (US-only hard gate + NYC bonus),
  `pay_extractor` (regex + focus-window anti-false-positive, tiered
  signal), `track_picker` (title rules → quant-description promotion →
  skill overlap → LLM tiebreaker; no firm list), `scorer` (deterministic
  combiner), `dedup` (canonical_key + 60-day per-company cap). **122 tests.**
- **Ingest** — `RawJob` + `JobSource` ABC, Greenhouse JSON client,
  Lever JSON client, `companies.yml` seeded with ~60 board tokens.
  **17 tests** using `httpx.MockTransport`.
- **Tracker** — SQLAlchemy 2.0 declarative models (`Job`, `Application`,
  `AnswerBankEntry`, `Event`, `SecurityEvent`), WAL + foreign-keys
  PRAGMAs, Alembic wiring with initial revision `0001_initial`. **17 tests.**
- **Execute** — `Applicator` ABC + `ApplyResult`, `standard_fields.resolve_all`,
  Greenhouse + Lever applicators (HTTP form-fetch + DRY_RUN payload dump).
  **24 tests.**
- **Review** — `gh_issues.render_issue_body` + `GitHubIssueClient`;
  `approval_listener.parse_command` + `handle_comment_event` with actor
  gate. **32 tests.**
- **Congregate** — `cover_letter.draft_cover_letter` with pluggable
  `Generator` protocol + `TemplateGenerator` (deterministic per-track
  narratives), `payload.build_submit_payload` with secret-field scrubbing.
  **13 tests.**
- **CLI** — `cli.py` Typer app with 7 commands (`init-db`, `profile-build`,
  `ingest`, `score`, `apply`, `security-report`, `review-handle`).
  Daily cap + pacing enforced in `apply`.
- **GitHub Actions** — `pipeline.yml` (cron */6h combined ingest+score+apply),
  `review-listener.yml` (on issue_comment with actor gate),
  `nightly.yml` (DB vacuum + archive + digest), `tests.yml` (PR matrix).

**Design decisions pinned this day** (unchanged since — kept for reference):

- **No predetermined quant firm list.** Track picking uses title keywords
  + description signal-weighting (strong=2, medium=1, threshold ≥4 promotes
  to quant) + LLM tiebreaker.
- **Deterministic answers first.** LLM only touches (a) cover letters and
  (b) novel unknown-type questions → the latter *always* route to review.
- **YOE never from LLM.** Computed at profile-build from the earliest
  resume mention per skill (union-of-intervals).
- **3-layer injection defense** (scanner → prompt hardening → output re-scan).
- **Private AutoApply repo + combined pipeline + polled approvals** keeps
  GH Actions usage under the 2000 min/mo cap.
- **Submodule, not copy.** `resumes/` is pinned to a SHA; user bumps with a
  single commit when recompiling resumes.
- **`DRY_RUN=True` by default.**

End-of-day tally: **328 tests passing.**

---

## 2026-04-17 — Phase 2: real submissions land

- **YOE hard filter** (`select/yoe_filter.py`). `extract_min_yoe` with
  strong vs. contextual patterns, soft-marker exclusions
  ("preferred"/"nice to have"/"a plus" — within 80 chars of the match),
  sentence-boundary awareness so a prior sentence's soft marker doesn't
  cancel a hard requirement in the next. Wired into `score` CLI. **55 tests.**
- **Playwright executor** — `execute/playwright_submit.py` with
  `submit_greenhouse` + `submit_lever`, stealth browser (playwright-stealth
  v1/v2 compat), `_fill_field` cascade (select → radio → checkbox → input
  with id/name fallbacks and case-insensitive CSS), `_fill_combobox`
  (React-Select with dynamic `aria-controls` scoping), `_detect_captcha`
  (reCAPTCHA / hCaptcha / Cloudflare keyword scan), IMAP OTP fetching
  (`_fetch_imap_verification_code`) with line-by-line code extractor
  and 8-box OTP layout support.
- **First real Greenhouse submission** — Applications Developer @
  Freedom Consulting. `/confirmation` URL reached; `Application` row
  written with `outcome="ok"`. Root causes hit and fixed: React-Select
  country field, `multi_value_single_select_fields` kind mapping, CSS
  attribute-selector `[]` escaping, Gmail UNSEEN search bug, 8-box OTP
  detection, HTML-only email body extraction.
- **Lever submission fixes** — extensive iteration. Root causes fixed:
  Lever hiding "file exceeds 100MB" error on 33KB PDFs (fixed by
  waiting for "Analyzing resume…" to disappear), React re-render wiping
  text fields after resume upload (reordered to upload → wait → fill),
  camelCase `urls[LinkedIn]` / `urls[GitHub]` DOM names (fixed `_BASE_FIELDS`
  + case-insensitive CSS), Lever qualifying-question cards in DOM but not
  in API (added `_fill_lever_cards` dynamic card resolver +
  `_card_heuristic_answer` rule table), EEO option mismatches (semantic
  fallback in `_fill_select`), ITAR eligibility select (`_snap_to_option`
  US-person-marker detection + "Not currently" fallback), Gemini 429s
  (extended template fallback in `llm_fallback.py`).
- **IMAP UTC timezone fix** — `SINCE` filter in `_fetch_imap_verification_code`
  was using UTC; local-date minus 1 day is more forgiving.
- **11 real Greenhouse submissions back-to-back.** All
  `field_errors=[]`. Confirmed: form-fetch → resolve_all → file upload →
  combobox fill → submit → OTP via IMAP → confirmation URL → `outcome="ok"`.
- **Remaining blocker identified** — Lever hCaptcha silently rejecting
  all submissions server-side. `HCAPTCHA_ACCESSIBILITY_TOKEN` injection
  plumbing added but token not yet obtained.

End-of-day tally: **486 tests passing.**

---

## 2026-04-18 — Lever captcha + infrastructure work

Intense day. Most of it was a deep dive trying to make Lever work from a
home IP; the code came out cleaner, but the conclusion is that Lever is
deferred (needs Bright Data). Work broken down by theme below.

### A. Double-apply dedup fix

Previous `scripts/apply_best_per_company.py` could re-submit to the same
company if an earlier `Application` row had a non-`ok` outcome. Now
excludes any job that appears in the `Application` table *at all*, belt-
and-suspenders filter for jobs with a real OK application even if their
status field was reverted by a transaction rollback.

### B. Rigorous submit-success detector

`playwright_submit._detect_submit_success()` replaced the loose "URL
contains /confirmation" check with a 5-stage decision ladder:

1. Hard-fail gates — visible error nodes or explicit failure phrases
   (e.g., `"please correct the"`, `"there were errors"`).
2. URL signal — `urlparse(page.url).path` contains a dedicated confirmation
   segment (`_STRONG_SUCCESS_URL_PATHS`) OR the query string contains a
   success marker (`_SUCCESS_QUERY_MARKERS`).
3. Strong phrase signal — page body contains a phrase from
   `_STRONG_SUCCESS_PHRASES` (18 entries) AND the submit button is no
   longer visible (guards against pre-submit hero copy).
4. Medium — submit button gone + generic affirmative wording
   ("thank you" / "submitted" / "received").
5. Legacy caller-provided fragments (lowest confidence).

Failure phrases (`_FAILURE_PHRASES`) kept deliberately narrow — generic
"required field" would match static form help text.

### C. SQLAlchemy 2.x environment upgrade

The anaconda base Python had SQLAlchemy 1.x; project needs 2.x. Upgraded
in-place so `python -m pytest` works without `.venv` activation, keeping
the same behavior inside `.venv`.

### D. Companies additions

- **Greenhouse `test_safe` additions (8):** `captivation`, `jjsnackfoods`,
  `commerceiq`, `ethoslife`, `ketryx`, `risingtidessolutionsllc`, `540`,
  `fanaticscollectibles`.
- **Lever `test_safe` additions (5):** `eightsleep`, `matterport`,
  `clipboardhealth`, `envoy`, `getro`.

### E. LLM fallback template expansion

`answers/llm_fallback.py` template branches added for onsite-willingness
("willing to work onsite" → "Yes"), full-address, street, line-2 / apt,
city, state, zip. Matching `state/answer_bank.yml` entries seeded
(`current_city`, `current_state`, `current_zip`, `street_address`,
`address_line_2`, `full_address`).

### F. Lever `curl_cffi` + generalized apply script

- `LeverApplicator` + `LeverSource` switched from `httpx` to `curl_cffi`
  with `impersonate="chrome124"`. Cause: `api.lever.co` edge silently
  hangs connections whose TLS handshake matches Python/OpenSSL's JA3
  fingerprint. With Chrome impersonation, requests return in 1–5 s.
  Tests still inject `httpx.MockTransport` clients via the existing
  `client=` kwarg — test path unchanged.
- Bumped Lever timeouts: API 20 → 45 s, Playwright `page.goto` 30 → 60 s.
- `scripts/apply_best_per_company.py` generalized with
  `--source greenhouse|lever|both` and `--board-token TOKEN` flags.
  `_best_per_company` keys by `source:company` so the same company on two
  boards doesn't collapse.

### G. US citizenship question split

`answers/types.QuestionType` added `US_CITIZEN` (yes/no), distinct from
`CITIZENSHIP` (country of citizenship). Classifier rule ordered before
the `CITIZENSHIP` regex so "Are you a U.S. citizen?" routes to the
new type with `_default: "No"`. `answer_bank.yml` updated.
`playwright_submit._card_heuristic_answer` fixed: citizenship returns
**No**, work-authorization returns **Yes**. Previously both returned
"Yes" because the single regex conflated them.

### H. CAPTCHA solver infrastructure

Large new subsystem — four providers, smart routing, two 2Captcha task
families.

- **`execute/captcha_types.py` (new, ~200 LOC).** `detect_captcha(page)`
  returns `CaptchaDetection(kind, site_key)` with kinds
  `hcaptcha_image`, `hcaptcha_token`, `recaptcha_v2`, `turnstile`.
  Detection signals: iframe host (`challenges.cloudflare.com`,
  `google.com/recaptcha/api2`, `hcaptcha`), site-key extraction from
  `data-sitekey` DOM attributes and iframe query strings, and text-scan
  inside hCaptcha frames for puzzle phrases to distinguish image-grid
  from idle widget.
- **`execute/captcha_solver.py` (new, ~450 LOC).** Four providers:
  `anticaptcha`, `capsolver`, `capmonster`, `2captcha`. All use JSON v2
  create/poll protocol. Public entry points:
  `solve_hcaptcha(provider, ...)`, `solve_recaptcha_v2_2captcha(...)`,
  `solve_turnstile_2captcha(...)`, `solve_2captcha_task(task_type, ...)`.
  **Explicit break:** `solve_hcaptcha(provider="2captcha")` raises
  `SolverError("2captcha no longer supports hCaptcha token solving …")`
  because 2Captcha has dropped hCaptcha from their current docs entirely
  (verified 2026-04-18 at https://2captcha.com/api-docs — hCaptcha is
  not listed on the index; attempts via legacy `method=hcaptcha` return
  `ERROR_METHOD_CALL`).
- **`execute/captcha_coords.py` (new, ~940 LOC).** hCaptcha image-puzzle
  solver — two task types depending on prompt classification:
  - **`GridTask`** (3×3 / 4×4 grids, "click each image containing X") →
    solution `click: [tile indices]` → convert to pixel centers + click.
  - **`CoordinatesTask`** (shape matching, "click the TWO shapes that are
    identical") → solution `coordinates: [{x, y}, ...]` → click pixel
    pairs.
  - `_classify_challenge(prompt)` inspects prompt text against
    `_GRID_PROMPT_PHRASES` and `_COORDS_PROMPT_PHRASES` (grid checked
    first to avoid "click each image" collisions).
  - Multi-round (up to 5), ~$0.0012 per round; real run today cost
    $0.0048 total to clear a 4-round shape puzzle.
  - `_find_puzzle_area_in_iframe` walks hCaptcha frames' inner DOM to
    locate the actual puzzle container inside the full-viewport enclave
    overlay (handles `#challenge`, `[role=dialog]`, `.challenge-view`,
    etc.; falls back to largest non-fullscreen visible element).
  - Annotated diagnostic screenshots — Pillow draws red circles at
    2Captcha's chosen click points before we click. Saved to
    `state/failed_submits/coords_clicks_<hash>.png` for visual
    verification on rejection. Pillow is a soft dep; if missing, raw
    PNG saves with no annotation.
  - `_dump_hcaptcha_state(page, tag)` — full state dump on solver abort:
    all hCaptcha iframe URLs + bounding boxes + inner text + submit
    button visibility + screenshot. Critical for understanding *why* a
    puzzle didn't render.
  - `_click_hcaptcha_checkbox` fallback — clicks the "I am human"
    widget if the puzzle doesn't render within 6 s.
- **Smart dispatcher** in `playwright_submit._maybe_solve_and_retry_captcha`:
  - `CAPTCHA_SOLVER=2captcha` auto-routes by detected kind: hCaptcha
    (any variant) → Grid path; reCAPTCHA v2 → token; Turnstile → token.
  - `CAPTCHA_SOLVER=2captcha_coords` forces the Grid/Coords path.
  - Other providers (anti-captcha / capsolver / capmonster) → token path
    for hCaptcha only.
- **`_wait_for_captcha(page, timeout=12s)`** — post-submit polling window
  because hCaptcha's challenge modal can take 5–15 s to mount.
- **`_wait_for_prompt(page, max_wait=30s)`** — patience inside the grid
  solver for the prompt text to actually render, plus the checkbox-click
  fallback.

### I. Diagnostic instrumentation

- Pre-submit field-state dump (Lever only): every `[name]` input's value,
  `unfilled_required` check with radio-group de-duping (so the "No" radio
  in a group where "Yes" is checked doesn't flag as unfilled).
- Post-submit screenshot + visible-error probe + hCaptcha iframe
  enumeration saved to `state/failed_submits/lever_<hash>.png` whenever
  the success detector reports failure.

### J. hCaptcha accessibility cookie path

- Correct signup URL (the 2026-04-17 log entry pointed at
  `accounts.hcaptcha.com/accessibility` — that's a 404; the right one is
  **`https://dashboard.hcaptcha.com/signup?type=accessibility`**).
- Injection plumbing in `submit_lever` (pre-navigation cookie on
  `.hcaptcha.com` domain with the env-configured
  `HCAPTCHA_ACCESSIBILITY_TOKEN`) confirmed working — when set, hCaptcha
  silent-passes without a challenge (no CAPTCHA detected in the run log,
  no solver invoked, no cost).

### K. ReviewFlag persistence

New `tracker.models.ReviewFlag` table:

```
id · job_id · application_id · field_name · field_label · field_kind
required · options (JSON) · reason · question_type · attempted_value
created_at · indexed on (job_id, question_type, reason, created_at)
```

Written by `persist_review_flags(session, *, job_id, application_id, flags)`
whenever an `ApplyResult.outcome="review"` lands. Populated with the
per-field reason (`unresolved` / `requires_llm` / `requires_review` /
`cover_letter_injection` / `captcha`) so we can later mine the questions
that blocked us most often. 3 new tests in `test_tracker.py`.

### L. Lever end-to-end verdict (important for future-me)

- Captcha solver loop works **end-to-end**: 4 rounds of shape-matching,
  hCaptcha challenge iframe closed cleanly, total cost $0.0048.
- With the accessibility cookie: hCaptcha never appears. Silent pass
  verified.
- **BUT** Lever's backend *still* rejects every submission from this IP
  with `"There was an error verifying your application. Please try again."`
  — regardless of whether the captcha was solved or silent-passed. This
  is Lever's own risk engine (likely IP reputation + fingerprint
  heuristics) kicking in beyond the hCaptcha layer.
- Comcast home IP was hit ~40+ times on `wyetechllc` alone today; it's
  thoroughly burned for Lever. University of Maryland VPN egress is also
  flagged. Cellular would probably help.
- **Architectural fix identified:** Bright Data Scraping Browser.
  Residential IP + clean browser fingerprint + captcha handled by their
  infra — replaces the IP, curl_cffi, and solver stacks with one vendor.
  ~$0.02–0.04/app. Deferred — see "Deferred work" below.

### M. ATS market-share research

Done today as decision support for expansion priorities. Numbers:

- **Fortune 500:** Workday ~37%, SuccessFactors ~13%, Taleo ~11%,
  iCIMS ~10%, Greenhouse ~5%, Lever ~2%.
- **Aadit's target mix (new-grad SWE/ML/HPC/quant):** Workday ~40–50%
  (FAANG, quant funds, defense), Greenhouse ~30–35% (unicorns, SaaS),
  Lever ~10–15% (mid-market SaaS), Ashby ~5–10% rising (OpenAI, Shopify,
  Linear), other ~5–10%.
- **Multi-ATS is common** — per Capterra's 2023 HR App Sprawl Survey,
  average HR dept runs 5 systems. A single company often lists roles on
  both Greenhouse (eng) and Workday (corp).
- **Takeaway:** shipping Greenhouse-only captures the biggest single ATS
  we can realistically automate (~30% of targets). Ashby is the
  highest-ROI *next* add (small but growing; simpler API than Lever's
  anti-bot layer).

### Test tally

**486 → 495 tests passing.** New test additions:
- `test_tracker.py` + 3 (`ReviewFlag` persistence)
- `test_answer_bank.py` + 3 (US citizen variants, country-of-citizenship)
- existing suites adjusted for new classifier rule ordering, no
  regressions.

---

## 2026-04-18 (continued) — Refactor + real Greenhouse submissions

Third session of the day. Two deferred TODOs shipped.

### N. Refactor `execute/playwright_submit.py` → `execute/submitter/` package

The big one: ``playwright_submit.py`` went from **2142 LOC in one file**
to a 175-LOC public shim + 10 focused modules under
``src/autoapply/execute/submitter/``. Package name is ``submitter/``
(not ``playwright/``) to avoid collision with the installed
``playwright`` Python package.

New layout:

```
src/autoapply/execute/
├── playwright_submit.py        ← public-API shim (175 LOC)
│                                  • re-exports CaptchaDetected + SubmitFailed
│                                  • keeps submit_greenhouse / submit_lever
│                                    as the public entry points
│                                  • calls into submitter/ internally
│
└── submitter/
    ├── __init__.py             ← CaptchaDetected + SubmitFailed (32 LOC)
    ├── util.py                 ← jitter, inner_text_safe,
    │                             strip_html, react_set_value (72 LOC)
    ├── field_fill.py           ← fill_field + fill_select/radio/combobox (274)
    ├── file_upload.py          ← upload_file + file_input_selector (62)
    ├── lever_cards.py          ← Lever qualifying-question resolver (189)
    ├── imap_otp.py             ← OTP detect + fetch + type (437)
    ├── success_detect.py       ← submit success/failure ladder (223)
    ├── captcha_detect.py       ← blocking-captcha probe + poll (197)
    ├── captcha_retry.py        ← solver dispatcher + token injection (249)
    ├── diagnostics.py          ← pre/post-submit Lever-scoped dumps (137)
    └── driver.py               ← _submit_form orchestrator + browser setup (365)
```

Public API at ``autoapply.execute.playwright_submit`` is **unchanged**:
``submit_greenhouse``, ``submit_lever``, ``CaptchaDetected``,
``SubmitFailed`` all exist at the same import path. Every test and every
applicator that did ``from autoapply.execute.playwright_submit import …``
keeps working without modification.

Rename of internal helpers: private-prefix ``_foo`` functions became
regular ``foo`` functions inside their now-dedicated module (since a
module is its own namespace). Example: ``_fill_field`` → ``field_fill.fill_field``.

Driver refactoring beyond extraction:
- Pre-submit diagnostic block (inline ~55 LOC) extracted to
  ``diagnostics.dump_pre_submit_state(page)``.
- Post-submit failure diagnostic block (inline ~40 LOC) extracted to
  ``diagnostics.dump_post_submit_failure(page, url)``.
- File-upload loop extracted to private ``_upload_all(page, files, field_errors)``.
- Email-verification handling extracted to private
  ``_handle_email_verification(...)`` which raises ``SubmitFailed`` on
  missing IMAP creds or OTP timeout.

``_submit_form`` itself dropped from **372 LOC to 220 LOC** (still the
orchestrator, but composed of well-named function calls rather than
inline blocks).

**Test tally: 495 → 495 passing.** No behavioral change.

### O. Real Greenhouse submissions through the refactored code

Ran ``python scripts/apply_best_per_company.py --source greenhouse
--no-dry-run`` on 8 unique companies, highest-scored-job-per-company.
3 new real submissions landed via the new ``submitter/driver.py``:

| # | Company | Rank | Track | Outcome | Notes |
|---|---|---|---|---|---|
| 1 | Captivation | 0.800 | swe | ✅ ok | Cloud SWE 1 — confirmation URL |
| 2 | Loop | 0.640 | swe | ✅ ok | SWE Full-Stack — confirmation URL |
| 3 | CommerceIQ | 0.640 | swe | 👀 review | Question flagged for LLM/human |
| 4 | Freedom Consulting | 0.600 | swe | ✅ ok | OTP fetched via IMAP + entered |
| 5 | Axon | 0.600 | ml | 👀 review | Axon-specific policy question |
| 6 | mthree | 0.600 | swe | ❌ failed | "School is required" (answer bank gap) |
| 7 | Smartsheet | 0.600 | swe | 👀 review | Applied-AI-rating question |
| 8 | Fanatics | 0.600 | swe | ❌ failed | "Location (City)" field gap |

Totals: **3 ok · 3 review · 2 failed**. The 2 failures are answer-bank
coverage gaps (School dropdown, City-only field — neither is in
``answer_bank.yml`` yet), not refactor regressions. The critical log
lines came through the new modules:

```
autoapply.execute.submitter.driver: playwright: navigating to ...
autoapply.execute.submitter.driver: email verification required; fetching code via IMAP …
autoapply.execute.submitter.driver: verification code fetched: 3zeVuN4w
autoapply.execute.submitter.driver: submit success (phrase: '...'): /confirmation
```

Cumulative real submissions to date: **14 OK** (11 yesterday + 3 today).

### Follow-up TODOs surfaced by this run

1. **`School` answer-bank entry** — mthree's "Select your school" dropdown
   failed. Need to either add a ``school`` field to the bank (currently
   resolved from Profile.education[0]) or fix the classifier to route
   dropdown "school" questions to Profile instead of failing with
   "School is required".
2. **`Location (City)` field** — Fanatics has a separate "Location (City)"
   input distinct from the full-address field. Already have
   ``current_city`` in the bank as of today's earlier commit; need to
   verify the classifier routes a bare "Location (City)" label to
   ``CURRENT_LOCATION`` with a city-only formatter, or add a dedicated
   ``LOCATION_CITY`` QuestionType.
3. **Axon / CommerceIQ / Smartsheet review-queue items** — inspect the
   per-field ``ReviewFlag`` rows in the DB to see which questions tripped
   the review route; decide whether they warrant new answer-bank entries
   or stay as `requires_review`.

### P. Question-type + classifier + bank extensions (addresses gaps from run O)

Triggered by the mthree School failure and the Fanatics/Smartsheet
"Location (City)" failure from section O. Today's changes tighten the
answer pipeline end-to-end:

- **New QuestionTypes** in ``answers/types.py``:
  - ``CURRENT_CITY`` / ``CURRENT_STATE`` / ``CURRENT_ZIP`` — bare-label
    atomic location fields ("City*", "Location (State)", "Zip code").
  - ``STREET_ADDRESS`` / ``ADDRESS_LINE_2`` / ``FULL_ADDRESS`` —
    street, apt/suite, and full-line variants.
  - ``REFERRAL_KNOW_SOMEONE`` — yes/no "do you know anyone here?" /
    "were you referred?" distinct from the name/email slots.

- **Classifier rules** in ``answers/classifier.py``:
  - Atomic location rules ordered **before** ``CURRENT_LOCATION`` so a
    bare "City*" label doesn't fall into the broader rule.
  - ``REFERRAL_KNOW_SOMEONE`` ordered **before** ``REFERRAL_NAME`` so
    "were you referred by an employee?" routes to the yes/no slot.
  - Broadened REFERRAL_NAME / REFERRAL_EMAIL to match "referrer" (not
    just "referral"/"referred").

- **Policy change**: ``REFERRAL_NAME`` / ``REFERRAL_EMAIL`` removed from
  ``REVIEW_REQUIRED``. They now resolve from the bank — default ``""``
  (empty = "leave the field blank"), auto-submittable.

- **Bank lookup semantics** in ``answers/bank.py`` — empty-string default
  values are now treated as VALID answers ("intentionally leave blank"),
  not as "missing → route to review". This is the one-liner change that
  unblocks referral-field defaults.

- **``state/answer_bank.yml``** — added entries:
  ```
  referral_know_someone: _default: "No"
  referral_name:         _default: ""
  referral_email:        _default: ""
  ```
  The existing ``current_city`` / ``current_state`` / ``current_zip`` /
  ``street_address`` / ``address_line_2`` / ``full_address`` entries
  (added in section E earlier today) are now actually reachable — before
  this session they were dead keys with no QuestionType routing them.

- **``fill_select`` normalized token-set matcher** in
  ``submitter/field_fill.py``. Waterfall now has five tiers — the new
  fifth is normalized-token matching that handles punctuation
  differences. Lets "University of Maryland, College Park" (profile)
  match "University of Maryland-College Park" (dropdown option) — the
  exact bug that tripped Greenhouse's school dropdown on mthree.

- **``--retry-non-ok`` flag** on ``scripts/apply_best_per_company.py``:
  re-pick jobs whose only prior ``Application`` rows had outcomes
  ``failed`` / ``review`` / ``captcha`` / ``dry_run``. Default dedup is
  unchanged (never re-submit to a real OK). Belt-and-suspenders: also
  exclude the whole **company** when any sibling posting already has a
  real OK submission, so we don't double-apply in one session.
  ``--include-statuses`` lets the caller pick which ``Job.status`` values
  to consider (default "scored"; under ``--retry-non-ok`` auto-widens to
  include "queued_review" and "applied_failed").

- **Driver changes** in ``submitter/driver.py``:
  - Augmented-data misses (DOM field absent) logged at DEBUG only, not
    added to ``field_errors`` — pre-augmented keys like ``location`` /
    ``city`` aren't present on every tenant and shouldn't produce noise.
  - Pre-submit diagnostic dump enabled for Greenhouse URLs too (was
    Lever-only) — needed to see the actual DOM field names on new-SPA
    tenants that inject fields outside the API.

- **``submit_greenhouse`` augmented data**: added ``location``, ``city``,
  ``state``, ``zip``, ``postal_code`` in addition to the existing
  ``country``. These are best-effort — silently skipped when the tenant
  DOM doesn't have the matching input.

- **Tests added** (495 → 520 passing):
  - 16 new classifier paraphrases for CURRENT_CITY / CURRENT_STATE /
    CURRENT_ZIP / STREET_ADDRESS / ADDRESS_LINE_2 / FULL_ADDRESS.
  - 6 new classifier paraphrases for REFERRAL_KNOW_SOMEONE /
    REFERRAL_NAME / REFERRAL_EMAIL.
  - ``test_referral_fields_resolve_to_deterministic_defaults`` —
    confirms the three referral types resolve from the bank without
    requiring review.
  - ``test_address_atom_fields_resolve_from_bank`` — confirms the six
    atomic address QuestionTypes pull from the bank with the expected
    values.
  - ``test_fill_select_normalize_tokens`` — punctuation-insensitive
    token-set equality, subset relationships, short-token filtering.

### Q. Retry run — lessons + remaining gaps

Re-ran ``scripts/apply_best_per_company.py --source greenhouse
--retry-non-ok --no-dry-run`` against the 8 companies (5 earlier-today
failures/reviews + 3 newly-ingested). Totals: **0 ok · 3 review · 5 failed**.

The 5 failures were NOT all the same issue as yesterday — each was a
different tenant-specific required field that isn't returned by
Greenhouse's ``questions`` API:

| Company | Blocked on |
|---|---|
| jjsnackfoods | School* dropdown required |
| CommerceIQ | Location (City)* required |
| mthree | Preferred pronouns* dropdown required |
| Smartsheet | Location (City)* required |
| Fanatics | Location (City)* required |

These all live in the DOM but not in the API — same pattern as the
``country`` combobox Greenhouse added to their new SPA, just more
fields. Now that the pre-submit diagnostic dump is enabled for
Greenhouse, the next debugging session will have the actual DOM
``name`` / ``id`` attributes for each of these fields and can augment
``submit_greenhouse`` accordingly.

### Follow-up TODOs (for the next session)

1. **Scrape + augment all Greenhouse-SPA-injected fields.** The
   pre-submit diagnostic dump now fires for Greenhouse — one failing run
   will expose every tenant-specific ``name`` / ``id`` on
   ``job-boards.greenhouse.io``. Add those to the ``augmented_data`` dict
   in ``submit_greenhouse``.
2. **School dropdown fill_select matching verification.** Run the
   diagnostic against jjsnackfoods specifically to confirm the school
   dropdown's option text format (does it include "University of
   Maryland" as a substring somewhere?). If not, the normalized-token
   matcher still won't win; may need a dedicated "school fuzzy match"
   helper.
3. **Preferred pronouns dropdown answer.** Add a ``DEMO_PRONOUNS`` bank
   default ("Prefer not to answer" / "He/Him" depending on preference).
   The type exists; the bank has no entry.

---

## Deferred work (future TODOs)

Order — one item remaining.

### 1. ~~Refactor `execute/playwright_submit.py`~~ ✅ Done 2026-04-18 (section N).

### 2. ~~Re-run Greenhouse apply at scale~~ ✅ Done 2026-04-18 (section O).

3 OK submissions across 3 new companies. See the outcome table above for the full run.

### 3. Eventually: Bright Data Scraping Browser for Lever

Only after (1) and (2) ship. Plan:

- Sign up for the $5 trial at `brightdata.com`; create a Scraping Browser
  zone; grab the `wss://brd-customer-hl_...-zone-scraping_browser:…@brd.superproxy.io:9222`
  endpoint.
- Add `BRIGHTDATA_SB_ENDPOINT` to `config.py` + `.env.example`.
- In the refactored driver, prefer
  `p.chromium.connect_over_cdp(BRIGHTDATA_SB_ENDPOINT)` when the env var
  is set, else keep `p.chromium.launch(headless=...)`.
- When Scraping Browser is in use, skip the captcha-solver path entirely
  (Bright Data handles captchas + IP rotation + TLS fingerprinting
  internally).
- Retry the same `--source lever --board-token wyetechllc` run — expect
  success.
- Then flip `TEST_SAFE_ONLY=false` to reach the `live_only` Lever
  companies (Mistral, Ramp, Gusto, Plaid, Mercury, etc.).

---

## Commands to remember

```bash
# Full test suite
python -m pytest -q                                      # 626 tests

# Single suite
python -m pytest tests/test_tracker.py -v

# Build profile from resume .tex files
autoapply profile-build

# Full local loop, DRY_RUN
autoapply ingest
autoapply score
autoapply apply --limit 5                                # DRY_RUN default

# Real submissions, one per Greenhouse company, top 10 by rank
python scripts/apply_best_per_company.py \
    --source greenhouse --no-dry-run --limit 10

# Single-board smoke test on Lever (after Bright Data lands)
python scripts/apply_best_per_company.py \
    --source lever --board-token whoop --limit 1 --no-dry-run

# Surgical re-apply to specific Job.id primary keys — useful for
# iterating on one tenant's form bugs without re-running the whole
# per-company picker. Reset status to 'scored' first.
python scripts/apply_by_job_ids.py 1285 1422 1058 1455 1368 --no-dry-run

# Apply Alembic migrations
alembic upgrade head

# Security report
autoapply security-report --days 7
```

---

## Historical milestones

- **2026-04-16** — Phase 1 foundations. 328 tests. All modules land with
  tests; nothing real submitted yet.
- **2026-04-17** — Phase 2 wired. 486 tests. First 11 real Greenhouse
  submissions land back-to-back with zero field errors. Lever hCaptcha
  identified as blocker.
- **2026-04-18** — Captcha stack + Lever verdict. 495 tests. hCaptcha
  solving verified end-to-end on real shape puzzles ($0.0048 per solve);
  accessibility cookie silent-pass verified; Lever backend still rejects
  from home IP → Bright Data deferred.
- **2026-04-19** — Playwright refactor + batch-LLM resolver. 626 tests.
  8/8 previously-failing Greenhouse apps submit (including 2 that were
  stuck in review on essay questions). See Q–T below.

---

## 2026-04-19 — Playwright refactor, Classifier overhaul, Batch LLM

Biggest architectural shift since Phase 2. Re-organized the submitter,
rewrote the location classifier, and moved from per-field LLM fallback
to a single batched Gemini call with full job-description context.
Result: every one of the 8 original problem apps (jjsnackfoods, mthree,
Smartsheet, Axon, Fanatics, CommerceIQ, Ketryx, Ophelia) now submits ok.

### N — `playwright_submit.py` split into `submitter/` package

Was 2142 LOC; now a 175-LOC shim re-exporting public API
(`submit_greenhouse`, `submit_lever`, `CaptchaDetected`, `SubmitFailed`)
from `submitter/` submodules:

- `driver.py` — orchestrator
- `field_fill.py` — fill_field / fill_select / fill_combobox (with
  preferred-pattern waterfall: exact → prefer-list → prefix → token-set
  → EEO decline → LLM)
- `file_upload.py` — resume + cover letter upload
- `lever_cards.py` — Lever qualifying cards
- `imap_otp.py` — email-OTP verification
- `success_detect.py` — post-submit success detection
- `captcha_detect.py` + `captcha_retry.py` — captcha flow
- `diagnostics.py` — pre-submit DOM state dump + failure screenshot
- `label_fallback.py` — hardcoded-value fallback by DOM label
- `dom_batch.py` — STAGE-2: DOM scrape + batched LLM for SPA-injected fields
- `util.py` — jitter, text, react_set_value

No behavioral change from the refactor itself — tests 495 → 495 green.

### O — Location/city classifier overhaul

Generic, not specific. Previously "City*" on Fanatics fell through to
UNKNOWN; `Location (City)` on Smartsheet hit the broader CURRENT_LOCATION
regex. Rewrote all three families (`CURRENT_CITY` / `CURRENT_STATE` /
`CURRENT_LOCATION`) to cover 80+ phrasings:

- **City**: bare labels, `City/Town`, `Current/Home/Your/Primary city`,
  `City of residence/residency`, `Location (City)` parenthetical variants
  (with ` `, `-`, `,`, `—`, `/` between), wh-questions
  (`What city do you live in?`, `In what city do you reside?`),
  imperative forms, metro-area aliases.
- **State**: bare labels including `US State`, `State/Province`,
  `State/Territory/Region`, qualified forms, state-of-residence,
  wh-questions, parenthetical `Location (State)`.
- **Location**: qualified forms, `where-do-you` variants with all tenses
  of live/reside/based/located/call-home/from, generic-address
  phrasings, combined city+state patterns.

Plus 10+ adjacent-context negatives that must NOT classify as location:
`City of birth`, `State of birth`, `Employer city`,
`Previous employer location`, `Job location`,
`Office location preference`, etc. Tests in
`test_answer_bank.py::test_location_adjacent_routes_to_unknown`.

Also fixed an `ADDRESS_LINE_2` bug where `\bunit\b` was missing word
boundaries — `Are you a veteran or active member of the United States
Armed Forces?` matched inside `United` and hijacked veteran questions.

### P — SPA-injected field handling (DOM batch Stage-2)

New `submitter/dom_batch.py`. The new `job-boards.greenhouse.io` React
SPA injects required fields (`School`, `Location (City)`, `Gender`,
`Race`) that aren't in Greenhouse's API `/questions` endpoint. Stage-1
can't see them. Stage-2 fires AFTER resume upload + SPA re-render:

1. Scrape empty required `input`/`select`/`textarea`/`checkbox`
   elements visible on the page.
2. For each `<select>` kind, open + scrape options. For async
   typeaheads (School — returns 0 options on open), type a seed
   (`"university of mary"` for School, `"computer"` for Major,
   `"bachelor"` for Degree) and re-scrape.
3. **Pre-resolve pass** — for each scraped field, try the deterministic
   classifier + Profile / bank first. Fills School/Degree/Major/Gender/
   Veteran/Hispanic/Location via Profile directly. No LLM tokens used.
4. **Late-scan rescan** — after pre-resolve, re-scrape for required
   fields that appeared only after the primary fills completed (Axon
   `Please identify your race*` renders only after gender is set).
5. **Batch-LLM fallback** — remaining unresolved fields go to the same
   `resolve_batch` call as Stage-1, with scraped option lists.

Each scraped element is **re-located fresh by id/name** before fill,
not reused from the initial scrape — React-Select unmounts and remounts
when sibling fields commit, and stale `Locator` refs cause fills to
leak to the next DOM element. (We saw `Computer Science` get typed
into `LinkedIn Profile` on jjsnackfoods before this fix.)

Maryland-only auto-check for multi-state hiring grids: when a checkbox
label matches a US state name, check only if it's in
`profile.willing_to_work_states`; else uncheck. Covers mthree's
"We hire in multiple locations; please select which you're 100%
committed to working in" — a 50-state + 6-territory grid where the
parent question text lives on a container our scraper doesn't climb to.

### Q — Gemini model cascade (v1 API)

Discovery: `gemini-2.0-flash` was **deprecated by Google in March 2026**.
The 429 `RESOURCE_EXHAUSTED` errors we were seeing weren't transient
rate-limiting; it had been removed from the free tier entirely.

Built a 4-model cascade in `llm_batch.py::MODEL_CASCADE` that tries
each in order, falling back on 404 / 429 / `PERMISSION_DENIED` /
`RESOURCE_EXHAUSTED`:

1. `gemini-3.1-flash-lite` — preview (currently 404s on v1; cascade
   rolls forward)
2. `gemini-2.5-flash-lite` — stable, 1000 RPD free tier (primary)
3. `gemini-3-flash` — preview
4. `gemini-2.5-flash` — stable, 250 RPD (final fallback)

Switched `google-genai` Client to `api_version="v1"` (stable). v1beta
supported a `response_mime_type: application/json` config flag we'd
been using for structured output; v1 doesn't, so removed that and
tightened the prompt instruction to output ONLY a JSON object. The
tolerant markdown-fence-stripping parser in `_parse_response` handles
any ` ```json ... ``` ` wrappers the model adds despite the directive.

The cascade also replaces the legacy `_call_gemini` in
`llm_fallback.py` so every Gemini call anywhere in the codebase
(scoring, per-field fallback, cover letter) goes through the same
4-model ladder.

### R — Single batched LLM call per application

New module `answers/llm_batch.py`. Rather than N per-field Gemini
calls for every essay / dropdown / novel-textarea, one call with the
full context:

- `profile` JSON (track-specific facts + common EEO/citizenship fields)
- `answer_bank_yaml` raw text (seeded defaults visible to the model)
- `<UNTRUSTED name="job_description">...` block — sanitized JD
- `<UNTRUSTED name="questions">...` block — all unresolved questions
  with `{id, label, type, required, options}` objects
- Structured output schema: `{"answers": [{id, value, source,
  confidence, reasoning}]}`

The prompt's rule block codifies:
- Never invent facts; ground everything in PROFILE + JD.
- F-1 OPT defaults (work_authorized=Yes, sponsorship_now=No,
  sponsorship_future=Yes, not a US citizen, not permanent, visa
  status = "F-1 visa OPT (STEM extension till 2029)").
- Threshold questions: compare profile value to the threshold in the
  label (fixes the old "Do you have GPA of 4.0+?" bug where the
  binary-Yes fallback picked Yes for a 3.975 GPA).
- Demographic questions: prefer decline/prefer-not-to-say variants.
- Subjective willingness questions (`Are you willing to...`, `Can you
  travel up to 25%?`, `Is it OK if...`): default Yes unless profile
  contradicts.
- Essay-length: 2–4 sentences citing concrete JD details; no
  superlatives.

Resolution pipeline in `standard_fields.py::resolve_all_batched`:

- Phase 1: existing classifier + Profile / bank + per-field `llm_fallback`
  (kept for select/multi_select only — the batch handles all text/textarea).
- Phase 2: collect required fields that Phase 1 couldn't resolve
  (unconfident selects + unresolved textareas), batch them in ONE
  call with JD context, backfill the resolved list. Machine-key
  fields (first_name, email, resume, etc.) NEVER batched — prevents
  LLM "helpfully" pasting a whole resume into `resume_text`.

Per-field audit log:

```
=== field audit: 24 answered, 0 unresolved ===
  [profile    ] first_name                     = 'Aadit'
  [classifier ] question_15298495008           = 'No'
  [llm_batch  ] question_15298505008           = 'Yes'
  [llm_single ] question_15298497008           = 'No'
```

Stored as `batch_audit` inside `Application.artifacts` JSON in the DB.

### S — Common-across-tracks Profile fields

`Profile` schema now carries immigration / EEO / willing-location
fields that are identical across SWE/ML/HPC/Quant (previously these
were hardcoded in the bank):

- EEO: `demo_gender`, `demo_race`, `demo_hispanic_latino`, `demo_veteran`,
  `demo_disability`, `demo_pronouns`, `demo_sexual_orientation`,
  `demo_transgender`
- Military: `military_service` (distinct from EEO veteran self-ID)
- Citizenship / immigration: `citizenship_country`, `us_citizen`,
  `work_authorized_us`, `permanent_work_authorization`,
  `require_sponsorship_now`, `require_sponsorship_future`,
  `visa_status` = `"F-1 visa OPT (STEM extension till 2029)"`
- Current location atoms: `current_city`, `current_state`,
  `current_state_full`, `current_zip`, `current_country`,
  `current_location`
- `willing_to_work_states` — list of 51 entries (50 states + DC).
  Used by `dom_batch.py` to auto-check Maryland on state-grid
  checkboxes.

These become `PROFILE_SOURCED` via an expanded frozenset. `bank.py`
`_from_profile` routes the new types. Bank entries remain as
fallbacks when `profile.json` is missing a field.

### T — Education preferred-pattern matching

Fixed the "University of Maryland - Baltimore" pick (Aadit's school is
College Park). Added `prefer_patterns` parameter to `fill_combobox`;
`dom_batch._fill_one` passes an ordered regex tuple for
School/Degree/Major. First pattern to match an option wins:

```python
_SCHOOL_OPTION_PREFERENCES = (
    r"^university\s+of\s+maryland[\s,\-–—/]+college\s+park$",  # exact
    r"\buniversity\s+of\s+maryland[\s,\-–—/]+college\s+park\b",
    r"\bumd[\s,\-–—/]+college\s+park\b",
    r"^university\s+of\s+maryland$",
    r"\buniversity\s+of\s+maryland\b",   # fallback, picks first UMD option
    r"\bumd\b",
    r"\bmaryland,?\s+college\s+park\b",
)
_DEGREE_OPTION_PREFERENCES = (
    r"^bachelor\s+of\s+science$",
    r"^b\.?s\.?$",
    r"^bachelor(?:'s)?(?:\s+degree)?$",
    ...
)
_DISCIPLINE_OPTION_PREFERENCES = (
    r"^computer\s+science$",
    r"^computer\s+and\s+information\s+sciences?$",
    ...
)
```

Verified live: jjsnackfoods picks "University of Maryland - College Park"
every time now.

### `resume_text` — from `.tex`, not LLM, not empty

Greenhouse tenants that render a paste-text textarea alongside the
file-upload input (some require it non-empty) now get a plain-text
version extracted from the SAME `.tex` file the PDF was compiled from.
New module `profile/tex_to_text.py` strips LaTeX preamble, commands,
and math mode; keeps content + section structure. `lru_cache`d per
track. No LLM round-trip; content is guaranteed to match the PDF.

### Essay questions routed to batch with JD context

- `WHY_ROLE`, `STRENGTHS`, `WEAKNESSES` moved from bank / `REVIEW_REQUIRED`
  into `LLM_REQUIRED` so they flow through the batch with JD context
  (previously `WHY_ROLE` was a per-track static template).
- `standard_fields.resolve_field` step 3.5 (per-field `llm_fallback`)
  now fires only for `select` / `multi_select` kinds. `text` /
  `textarea` novels flow straight to Phase 2 so they get the JD.
- `AGREE_TO_TERMS` classifier regex broadened to catch "I agree",
  "I consent", "By checking this box", "Please accept the terms",
  GDPR / privacy / SMS consent variants.

Live output for Ketryx's "Why do you wish to join a startup?" essay:
> "I am drawn to the dynamic and fast-paced environment of a startup like
> Ketryx, where I can directly contribute to building innovative
> products..."

Grounded in the JD. Similar tailoring observed on Ophelia's 2 essay
textareas and the Smartsheet AI-familiarity Likert dropdown.

### New classifier types

- `MILITARY_SERVICE` — "Military Service*", "Have you served in the
  Armed Forces?", "Active duty military" → No. Distinct from
  `DEMO_VETERAN` (EEO self-ID). Previously an `llm_single` field that
  sometimes answered "Yes" for Aadit.
- `PERMANENT_WORK_AUTHORIZATION` — "Do you have the permanent and
  unrestricted right to work in the US?" → No. Distinct from
  `WORK_AUTHORIZED_US` which OPT satisfies.
- `WILLING_WORK_LOCATION` — "Are you willing to work from our Sterling,
  VA office?", "Based out of...", "Onsite 4 days/week" → Yes
  (subjective-willingness default).
- `DEMO_GENDER` regex broadened to match "I identify my gender as"
  (Smartsheet phrasing).

### Live verification results

8/8 previously-failing apps submitted successfully in the final re-run:

| App                    | Prior                          | Fixed by                                                 |
|------------------------|--------------------------------|----------------------------------------------------------|
| jjsnackfoods           | School = Baltimore, Military=Yes | prefer_patterns regex, PROFILE_SOURCED military_service  |
| mthree                 | 50-state grid, GDPR consent    | willing_to_work_states lookup, AGREE_TO_TERMS classifier |
| Smartsheet             | 5-option EEO dropdowns         | DEMO_GENDER classifier broadened, llm_batch option-match |
| Axon                   | Race rendered late             | late-scan rescan pass                                    |
| Fanatics               | Location (City) SPA-injected   | dom_batch stage-2                                        |
| CommerceIQ             | Location (City) SPA-injected   | dom_batch stage-2                                        |
| **Ketryx (new!)**      | "Why startup?" essay → review  | batch LLM + JD context in prompt                         |
| **Ophelia (new!)**     | 2 essay textareas → review     | batch LLM + JD context in prompt                         |

Ketryx + Ophelia had never submitted before (3 review-only attempts
each over the prior day). They submit cleanly now because the batched
LLM gets the JD and writes tailored essays.

### Tests

520 → 626 passing. Added:

- `tests/conftest.py` — autouse fixture that blocks live Gemini calls
  + clears `GEMINI_API_KEY`. Every LLM-involved test must monkeypatch
  a scripted stub; no test ever hits the live API.
- `tests/test_llm_batch.py` — 32 tests covering: model cascade order,
  cascade-error detection (429 / 404 / `PERMISSION_DENIED` / auth),
  `_validate_select_value` exact + case-insensitive + substring
  rejection, response parser (happy path, markdown fences, low
  confidence forced to review, multi-select, missing questions
  backfilled, invalid JSON), `resolve_all_batched` end-to-end
  (Phase 1 answers everything → no batch call, required select
  unresolved → batch → backfill, optional fields skipped, LLM
  decline preserves review state).
- `tests/test_answer_bank.py` — +125 cases covering the generic
  location regex families, adjacent-context negatives, Military
  Service, Permanent Work Authorization, Willing Work Location,
  education-option preferences, checkbox handling, the regressions
  from the ADDRESS_LINE_2 `\bunit\b` word-boundary bug.

### Commits

- `3a12bf4` refactor: split playwright_submit.py (2142 LOC) into submitter/ package
- `2bb84fa` feat: city/state/zip/address QuestionTypes, referral defaults, --retry-non-ok
- `24fdde3` feat: batch-LLM resolver + DOM stage-2 + .tex-sourced resume_text
