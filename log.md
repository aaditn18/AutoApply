# AutoApply — Implementation Log

Living record of what's been built and what works. Updated as work progresses.
Pair with `/Users/aaditnilay/.claude/plans/drifting-snuggling-harbor.md` — the
plan is the target, this log is the current state.

---

## ✅ Done

### Phase 1 — Bootstrap

- **Repo scaffolding** (2026-04-16)
  - `pyproject.toml` (Python ≥3.11, all MVP deps declared, entrypoint `autoapply=autoapply.cli:app`, pytest pythonpath=src)
  - `.gitignore`, `.gitattributes` (sqlite marked binary), `.env.example`, `README.md`
  - `.gitmodules` pinning `resumes/` to `git@github.com:aaditn18/aadit_nilay_resume_swe.git` branch main
  - Full `src/autoapply/` package tree with `__init__.py` stubs for: `answers/`, `congregate/`, `execute/`, `ingest/`, `profile/`, `review/`, `security/`, `select/`, `tracker/`
  - `state/dry_runs/.gitkeep` placeholder
  - `.venv/` created locally with pytest installed (not committed)

- **`src/autoapply/config.py`** — central Pydantic-settings Settings class; env-driven; singleton `get_settings()`; properties `database_url`, `profile_json_path`, `answer_bank_path`

### Security layer (the CRITICAL module)

- **`src/autoapply/security/injection_guard.py`** — three-layer prompt-injection guard
  - `InjectionKind` enum (12 kinds): AI_ADDRESS, IGNORE_INSTRUCTIONS, DISREGARD_PROMPT, SYSTEM_PROMPT_LEAK, FORCED_RESPONSE, INCLUDE_CANARY, VERBATIM_COPY, CHAT_TEMPLATE_TOKEN, JAILBREAK_MARKER, ZERO_WIDTH_UNICODE, ROLE_HIJACK, MARKDOWN_SYSTEM
  - `InjectionHit`, `InjectionReport`, `InjectionDetected` dataclasses/exception
  - Public API: `scan()` (detect), `sanitize()` (strip zero-width + NFKC + redact), `validate_output()` (post-LLM canary re-scan, raises)
  - AI_ADDRESS pattern extended after test failures: now also catches `note to any LLM`, `any AI reading`, `dear LLM`, etc. (not just `if you are an AI`)
  - FORCED_RESPONSE pattern extended to capture the full quoted canary (e.g., `you must begin with "PINEAPPLE"` now fully redacts PINEAPPLE, not just the verb)
  - INCLUDE_CANARY pattern now allows unquoted ALL_CAPS canaries (`ROOT_ACCESS`, etc.)

- **`tests/test_injection_guard.py`** — 109 tests, all passing
  - 30 synthetic injected JDs at `tests/fixtures/injection_jds/NN_<slug>.txt`, 100% detection
  - 5 clean JDs at `tests/fixtures/clean_jds/` (incl. tricky wording like "you must have 4+ years", "override the default configuration"), 0 false positives
  - Redaction tests: every injection fixture has `[REDACTED]` marker OR zero-width chars stripped
  - "Canary-enclosed" tests: for 6 fixtures where the regex span fully covers the canary word (FORCED_RESPONSE and INCLUDE_CANARY kinds), assert the canary is completely removed
  - `validate_output()` raises on chat-template tokens or detected injection markers
  - Span-offset sanity check (scan spans index into original text)
  - Empty/whitespace edge cases

**Verification:** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_injection_guard.py -v` → **109 passed**

---

### Phase 1 — Answers layer (2026-04-16)

- **`src/autoapply/answers/types.py`** — `QuestionType` enum with 50+ values
  covering identity, contact, work-auth, YOE, education, compensation,
  essays, referral, demographics, prior employment, consents, and UNKNOWN
  fallback. Three policy frozensets: `PROFILE_SOURCED` (18 types pulled
  from parsed Profile), `LLM_REQUIRED` (why_company, cover_letter_body),
  `REVIEW_REQUIRED` (unknown, strengths, weaknesses, referral fields).

- **`src/autoapply/answers/classifier.py`** — rule-table classifier.
  - `ClassifiedQuestion` dataclass (type, confidence, slot, match_text, original, source)
  - `_SKILL_ALIASES` dict (~35 aliases: py→Python, cpp→C++, k8s→Kubernetes, etc.)
  - `_canon_skill()` canonicalizer + `_SKILL_STOPWORDS` noise filter
  - `_RULES` — ~50 ordered regex rules, specific-first
  - Rule-firing order audit: YOE_LANGUAGE explicit → YOE_GENERAL → YOE_LANGUAGE bare → sponsorship (future then now) → work-auth → visa → citizenship → contact → identity → location → availability → education → compensation → demographics (hispanic_latino FIRST to avoid "ethnicity" collision) → prior employment → referrals → essays → consents
  - `classify()` entrypoint; YOE_LANGUAGE match is rejected if captured skill is a generic stopword ("work", "relevant", etc.) and falls through to YOE_GENERAL
  - `_embedding_fallback()` stub — deferred

- **`src/autoapply/answers/bank.py`** — `Answer` dataclass + `AnswerBank` class
  - Routing priority: REVIEW_REQUIRED → LLM_REQUIRED → PROFILE_SOURCED → bank lookup
  - `lookup(qt, track)` returns (value, source) with per-track over `_default`
  - `answer(cq, profile, track)` is the main entrypoint; returns `Answer` with exactly one of `value` / `requires_llm` / `requires_review`
  - `resolve_raw(raw, ...)` classify-and-resolve in one shot for tests
  - `_from_profile()` maps PROFILE_SOURCED types to Profile fields:
    - name splitting (first/last/preferred), email, phone, linkedin, github
    - education[0] → school/degree/major/minor/gpa/graduation_date
    - YOE_LANGUAGE → `profile.years_of_experience[slot.skill]` with case-insensitive lookup; missing skills return "0" (forms need numeric)
  - `_format_yoe(years)` — "3" for whole years (within 0.1 tolerance), "1.5" for fractional; under-1 positive rounds up to "1"
  - `_extract_major()` — strips "B.S.", "Bachelor of Science in", etc.

- **`state/answer_bank.yml`** — seed with ~25 entries covering all
  bank-routed types: work_authorized_us, sponsorship (now/future),
  visa_status, citizenship, yoe_general, current_location,
  willing_to_relocate, available_start_date, notice_period,
  salary_expectation (per-track), hourly_rate, why_role (per-track),
  how_heard_about, demographics (all "Decline to self-identify"),
  previously_employed_here, currently_employed_elsewhere, consents.

- **`tests/test_answer_bank.py`** — 75 tests, all passing
  - 55 classifier paraphrase cases across 22 question types
  - Slot extraction tests for skill aliases (py/cpp/k8s)
  - UNKNOWN routing for truly novel questions
  - Policy routing: REVIEW_REQUIRED (5 types) and LLM_REQUIRED (2 types) honored
  - PROFILE_SOURCED resolution: email, LinkedIn, GPA, school, degree, first/last name
  - Graduation date formatting ("May 2026" from DateRange)
  - YOE_LANGUAGE profile lookup + case-insensitive fallback + unknown-skill "0"
  - Bank per-track-beats-default check on WHY_ROLE
  - Bank `_default` fallback check on CURRENT_LOCATION
  - Synthetic missing-entry test → routes to review
  - Raw end-to-end: "Are you authorized to work in the US?" → "Yes"
  - WHY_COMPANY forced through LLM even though bank could answer
  - Coverage check: every non-policy type has a bank entry
  - `_format_yoe` rounding edge cases; `_extract_major` parsing

**Verification:** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_answer_bank.py -v` → **75 passed**

---

### Phase 1 — Selection / ranking (2026-04-16)

- **`src/autoapply/select/location_filter.py`** — US-only hard filter + NYC bonus
  - `country_from_location(raw)` — ISO code lookup; handles 40+ non-US country/region markers (UK, Canada, India, EMEA, etc.) AND US cities/states/abbreviations/"Remote (US)" forms
  - `is_us_location(raw)` — True for US markers; True for bare "Remote" (no country signal) to avoid dropping ambiguous postings; False only for unambiguous non-US
  - `nyc_bonus(raw)` → 0.15 for NYC-adjacent postings (Manhattan, Brooklyn, Jersey City, etc.), 0.0 otherwise

- **`src/autoapply/select/pay_extractor.py`** — deterministic pay extraction
  - `PayInfo` dataclass (low/high/midpoint annualized USD + original unit + source_span)
  - Regex handles ranges ($120,000 - $160,000, $120k-$180k, $50-$70/hour), em/en-dashes, "between X and Y", single-value-with-unit ($175k/year)
  - `_focus_window` restricts scanning to salary-keyword neighborhoods (kills false positives on "$1.5B in fees saved")
  - Sanity range 30k–2M annual; hourly × 2080 for annualization
  - `pay_signal(midpoint)` → tiers: ≥200k=0.30, ≥160k=0.22, ≥130k=0.14, ≥100k=0.08, <100k=0.0, None=0.10 (neutral)

- **`src/autoapply/select/track_picker.py`** — resume-track selection (NO firm list)
  - `TrackDecision` dataclass (track/reason/stage/quant_weight/scores)
  - Ladder: strong title keyword → quant-description promotion (weight ≥4) → skill-overlap margin (≥0.15) → LLM tiebreaker → tie
  - Title tables: _QUANT_STRONG_TITLE (quant/quantitative/trader/hft/systematic/alpha researcher...), _HPC_STRONG_TITLE (cuda/gpu/compiler/performance eng...), _ML_STRONG_TITLE (ml engineer/research engineer/nlp/cv/llm/deep learning), _SWE_STRONG_TITLE (software engineer/backend/full stack/platform/sre)
  - QUANT_DESC signals: 15 strong (weight=2: alpha/market making/stat arb/order book/tick data/FIX/microstructure/backtest/sharpe/pnl/execution algos/low-latency trading), 12 medium (weight=1: derivatives/options/volatility/portfolio optimization/hedging/monte carlo/bps/liquidity/bid-ask/trading strategy/quant research)
  - Injection flag short-circuits with track=None (route to review)

- **`src/autoapply/select/scorer.py`** — deterministic combiner
  - `score_job(base_fit, description, location)` → `ScoredJob` with pay_midpoint/pay_signal/loc_signal/final_rank + human-readable reasons
  - final_rank = base_fit + pay_signal + loc_signal when US, 0.0 when non-US
  - Every factor stored separately → offline re-ranking without re-calling LLM

- **`src/autoapply/select/dedup.py`** — canonical key + hard filters
  - `canonical_key(company, title, location)` → 16-char SHA256 prefix; strips title noise (Remote/Hybrid/On-site/I/II/III/senior/sr/jr/lead/staff/principal/new grad) so parallel postings collapse
  - Senior/Staff/Jr. prefixes stripped iteratively (handles "Senior Staff Engineer")
  - `filter_hard(is_us, injection_detected, company, recent_company_applications, today)` → FilterResult; 60-day per-company cap; location filter runs first (saves a scoring LLM call)

- **Tests — 122 new, all passing**
  - `tests/test_location_filter.py` — 18 US accepts + 12 non-US rejects + country_from_location + NYC bonus + edge cases (45 tests)
  - `tests/test_pay_extractor.py` — 22 tests: ranges, k-suffix, em-dashes, hourly annualization, "between X and Y", single-with-unit, false-positive avoidance, pay_signal tiers
  - `tests/test_track_picker.py` — 38 tests: 30 strong-title assertions (6 SWE, 8 ML, 6 HPC, 9 quant titles), 5 quant-in-description promotion (generic titles + trading-heavy desc), injection block, LLM tiebreaker, REVIEW passthrough
  - `tests/test_dedup.py` — 11 tests: canonical key collapse, title-noise strip, seniority collapse, company+title distinction, hard-filter 4-way matrix
  - `tests/test_scorer.py` — 4 end-to-end combinations (non-US zero, US+NYC+pay stack, undisclosed neutral, low pay)

**Verification:** `PYTHONPATH=src .venv/bin/python -m pytest -v` → **328 passed**

---

### Phase 1 — Ingest layer (2026-04-16)

- **`src/autoapply/ingest/base.py`** — `RawJob` dataclass + `JobSource` ABC
  - RawJob fields: source, source_id, board_token, url, title, company, location, department, description, posted_at, updated_at, employment_type, metadata
  - Every source yields uniform RawJobs

- **`src/autoapply/ingest/greenhouse.py`** — public JSON API client
  - Endpoint: `https://boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true`
  - `_strip_html` — preserves word boundaries (block-level tags → newlines), handles common entities, collapses whitespace
  - `_first_location` handles both `location.name` and `offices[].name` shapes
  - `_company_from_metadata` falls back to prettified board_token
  - httpx.Client with User-Agent + 20s timeout; HTTP errors logged + yielded empty

- **`src/autoapply/ingest/lever.py`** — public JSON API client
  - Endpoint: `https://api.lever.co/v0/postings/<token>?mode=json`
  - `_flatten_description` concatenates `descriptionPlain` + `lists[]` (header + content) + `additionalPlain`
  - `_location` handles both scalar and list `allLocations`
  - `_department` joins department/team/commitment

- **`src/autoapply/ingest/companies.yml`** — 60 seed board tokens
  - Greenhouse (~45): Big tech (Airbnb, Figma, Stripe, Reddit, Notion...), AI labs (Anthropic, OpenAI, Scale AI, Databricks, HuggingFace, Perplexity), infra (HashiCorp, MongoDB, Vercel, Anyscale), quant (Two Sigma, Citadel, Jump Trading, DRW, Optiver, IMC, HRT, Akuna, Cubist, PDT, Qube), HPC (NVIDIA, Cerebras, Groq, SambaNova, Tenstorrent)
  - Lever (~15): AI (Mistral, Writer, Together, Runway), SaaS/fintech (Ramp, Gusto, Plaid, Mercury, Checkr), Netflix, Benchling, Attentive

- **Tests — 17 new, all passing via `httpx.MockTransport`**
  - HTML strip (5): basic tags, word boundaries preserved, entity decoding, whitespace collapse, empty
  - Greenhouse helpers (5): first_location (dict/offices/missing), company_from_metadata (payload/token-fallback)
  - Greenhouse end-to-end (2): happy path parses 2 mock jobs with full RawJob shape; HTTP 500 yields empty
  - Lever end-to-end (2): happy path 2 mock jobs with list/description concatenation; location list flattening
  - Lever helpers (2): location list, description concatenation
  - Base contract (2): JobSource is abstract, RawJob defaults

**Verification:** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_ingest.py -v` → **17 passed**

---

## 🟡 In progress

(nothing — ingest layer complete; moving to tracker + execute)

---

## ⏳ Remaining (from plan, in rough order)

- [x] **`profile/schema.py` + `profile/tex_parser.py` + `profile/build.py`** (2026-04-16)
  - `schema.py`: Pydantic models for DateRange, Experience, Project, Education, Skills, Profile
  - `tex_parser.py`: brace-balanced arg extractor; section-body splitter; macro iterators for `\eduSubheading`, `\resumeSubheading`, `\resumeProjectHeading`, `\resumeItem`; date-range parser ("May 2025 -- Aug 2025", "Feb 2026 -- Present", "CVPRW 2026"); `_strip_latex` for `\textbf`/`\emph`/`\underline`/`\textit`/`\text`/`\href`/escape chars; skills-category extractor
  - `build.py`: orchestrates all 4 tracks, writes `state/profile.json` keyed by track
  - YOE computed via union-of-intervals across overlapping experiences (e.g., AWS appears in 3 experiences on SWE resume → 0.9y union, not sum)
  - Smoke-tested against real `.tex` files: swe (4 exp / 3 proj / 3 skill buckets), ml (4 exp / 0 proj), hpc (4 exp / 0 proj), quant (5 exp / 3 proj)
  - **22 tests in `tests/test_tex_parser.py`, all passing** (low-level helpers + date parsing + YOE math + 4 end-to-end real-resume tests + idempotency check)

- [x] **`answers/types.py` + `answers/bank.py` + `answers/classifier.py` + `state/answer_bank.yml`** (2026-04-16)
  - 75 tests green; embeddings fallback stubbed (not wired until a paraphrase leaks past the rule table)

- [x] **`select/location_filter.py`** + **`select/pay_extractor.py`** + **`select/track_picker.py`** + **`select/scorer.py`** + **`select/dedup.py`** (2026-04-16) — 122 tests green

- [x] **`select/yoe_filter.py`** (2026-04-17) — 55 tests green
  - `extract_min_yoe(text)` → int | None: two-phase regex extraction (strong patterns always fire; contextual patterns require a pre-context requirement word within 300 chars)
  - Strong patterns: `N+`, `minimum N`, `at least N`, `N or more`, range `N-M of experience`, `N years of experience required/needed/minimum`
  - Contextual patterns: `N years of professional/relevant/… experience` (needs nearby "require/qualifications/must have")
  - Soft-marker exclusions: "preferred", "nice to have", "a plus", "ideally", "desired", "bonus if", "not required" — any of these within 80 chars (same sentence) suppresses the match
  - Sentence-boundary awareness: soft markers from a prior sentence (separated by `.`, `!`, `?`, `;`, `\n`) do NOT suppress a hard requirement in the current sentence
  - `is_yoe_eligible(text)` → True when no requirement found OR min ≤ 2; False when min > 2
  - Wired into `cli.py` `score` command between hard-filters and track picking; adds `rej_yoe=N` to score output; uses `rejected_by_yoe` status (already in STATUSES)

- [x] **`ingest/base.py` + `ingest/greenhouse.py` + `ingest/lever.py` + `ingest/companies.yml`** (2026-04-16) — 17 tests green, 60 seed board tokens

- [x] **`tracker/models.py` + `tracker/db.py` + Alembic migrations** (2026-04-16)
  - SQLAlchemy 2.0 declarative models: Job (unique canonical_key), Application (dry_run default True), AnswerBankEntry, Event, SecurityEvent (append-only)
  - `db.py`: WAL + foreign_keys PRAGMAs on real engine; in-memory engine for tests; `session_scope()` context manager
  - Alembic wired via `alembic.ini` + `migrations/env.py` (reads URL from Settings, `render_as_batch=True` for SQLite ALTER); initial revision `0001_initial.py`
  - 17 tests green: canonical_key uniqueness, cascade delete, (question_type, track_key) uniqueness, rollback on exception, status round-trip

- [x] **`execute/base.py` + `execute/standard_fields.py` + `execute/greenhouse_apply.py` + `execute/lever_apply.py`** (2026-04-16) — 24 tests green
  - `standard_fields.resolve_field()` — machine-key → profile attr first, then classifier → bank/profile. Required + no answer raises `UnresolvedField`; optional + no answer returns empty. LLM/review-required only blocks for required fields
  - `_snap_to_option()` — select fields get snapped to case-insensitive substring match of available options (e.g., "Yes" bank value → "Yes" option literal)
  - `Applicator.apply(job, dry_run=True)` — DRY_RUN is default; any unresolved-required or llm/review-required on required field → `outcome="review"` with review_reasons list; clean resolve → dumps payload JSON to `state/dry_runs/<canonical_key>.json`
  - `GreenhouseApplicator.fetch_form` — GET `/v1/boards/<token>/jobs/<id>?questions=true`; one question can expose N sub-fields (demographics compound), each becomes one `FieldSpec`
  - `LeverApplicator.fetch_form` — concatenates base fields (name/email/phone/resume/urls[linkedin]/github/portfolio/cover_letter) with `customQuestions` + `additionalQuestions` namespaced as `cards[<qid>]`
  - **Both applicators' `submit()` is disabled by default** — returns `outcome="failed"` with `error_code="submit_disabled"`. Real submission requires CSRF harvesting via Playwright (Phase 2)

- [ ] **Playwright executor (Phase 2)** — wraps `submit()` after CSRF token harvesting; stealth + 30–120s pacing + 60/day cap

- [x] **`review/gh_issues.py` + `review/approval_listener.py`** (2026-04-16) — 32 tests green
  - `render_issue_body()` — deterministic Markdown with Scoring / Proposed-answers / Unresolved / Why-in-review / Cover-letter sections + command cheat-sheet + hidden `<!-- autoapply:job_canonical_key=... -->` marker
  - `build_payload()` — labels: `autoapply:review`, `track:<track>`, `security:injection-detected` (when flagged), `has-cover-letter`
  - `GitHubIssueClient` — REST wrapper (`create_issue`, `close_issue`, `add_labels`); caller-supplied client gets auth headers injected via `setdefault`
  - `approval_listener.parse_command` — `/approve`, `/approve --track=ml`, `/reject <reason>`, `/snooze`; command MUST be on first non-empty line; invalid track flag still approves but drops override
  - `handle_comment_event()` — full webhook payload parser; gates on `action ∈ (created, edited)`, actor matches `Settings.GH_REPO_OWNER` (case-insensitive), issue body carries canonical-key marker

- [x] **`congregate/cover_letter.py` + `congregate/payload.py`** (2026-04-16) — 13 tests green
  - `draft_cover_letter()` — sanitizes JD via `injection_guard.sanitize` → `<UNTRUSTED>`-wrapped prompt → generator (pluggable `Generator` protocol: default `TemplateGenerator` is LLM-free; LLM wrappers plug in at `cli.py` when API key is set) → `scan()` output → raises `CoverLetterRejected(report)` on output injection hit so orchestrator can log `SecurityEvent` and route to review
  - `TemplateGenerator` — deterministic per-track narratives (swe/ml/hpc/quant) wired to Aadit's actual experience (PayPal, PSSG, CUDA, MPI+OpenMP Monte Carlo)
  - `build_submit_payload()` — merges applicator `{data, files}` + cover-letter text + resume path; scrubs secret-keyed fields (`password`, `token`, `api_key`, `session_cookie` — case-insensitive)
  - `to_log_dict()` truncates long values for audit logs without breaking semantics

- [x] **`cli.py`** (2026-04-16) — Typer app with 7 commands
  - `init-db` — idempotent `Base.metadata.create_all`; call once then hand off to Alembic
  - `profile-build` — delegates to `profile.build.run()`; writes `state/profile.json`
  - `ingest --source greenhouse|lever --board TOKEN --limit N` — fetches boards from `companies.yml`, upserts jobs per canonical_key, emits `Event(kind="ingested")`, each job in own `session_scope` for fault isolation
  - `score --limit N` — for each `status='new'` job: `scan()` + SecurityEvent on hit + `filter_hard()` + `pick_track()` + `extract_pay()` + `pay_signal()` + `nyc_bonus()` + `base_fit=0.5` placeholder; sets status=`scored`/`rejected_*`
  - `apply --no-dry-run --limit N --min-rank F` — iterates `scored` by `final_rank desc`; drafts cover letter (catches `CoverLetterRejected` → SecurityEvent + `queued_review`); dispatches to `GreenhouseApplicator` or `LeverApplicator`; logs `Application` row + `Event`; DRY_RUN=true by default
  - `security-report --days N` — dumps SecurityEvent rows as JSON within the window
  - `review-handle --event <path>` — parses `GITHUB_EVENT_PATH` JSON → `handle_comment_event()` → prints decision JSON; real apply gated behind `GH_ISSUES_PAT`

- [x] **`.github/workflows/`** (2026-04-16) — all 4 workflows YAML-validated
  - `pipeline.yml` — combined ingest→score→apply, cron `0 */6 * * *`, concurrency-group `autoapply-pipeline`, auto-commits `state/jobs.sqlite`; `workflow_dispatch` inputs for `dry_run` + `skip_apply`
  - `review-listener.yml` — on `issue_comment (created|edited)`, YAML pre-filter on slash-command prefix, actor gate vs `GITHUB_REPOSITORY_OWNER`; serializes in same `autoapply-pipeline` concurrency group to prevent SQLite write races
  - `nightly.yml` — cron 05:00 UTC: `VACUUM + ANALYZE`, 90-day archive, profile rebuild, 7-day security report, auto-commit
  - `tests.yml` — on PR and push to main, matrix Python 3.11+3.12, ruff lint + pytest + CLI smoke; concurrency cancel-in-progress per branch

- [x] **DRY_RUN end-to-end smoke test** (2026-04-16)
  - 4 synthetic jobs seeded: Jane Street (quant, NYC, $215k → rank 0.950), NVIDIA (HPC, CA, $175k → rank 0.720), Acme London (rejected_by_location), BadCorp injected JD (rejected_by_injection)
  - `Applicator.apply(dry_run=True)` verified end-to-end: mock Greenhouse form → `resolve_all` → `_dump_dry_run` → `state/dry_runs/<ck>.json` with keys `[applicator, job, payload, resolved, resume_path, track, ts]`

---

## 🧠 Design decisions pinned (for future-me)

- **No predetermined quant firm list.** Track picking uses title keywords + description signal-weighting (`QUANT_DESC_SIGNALS`, strong=2 medium=1, threshold ≥4 promotes to quant) + LLM tiebreaker. Design requirement confirmed by user.
- **Deterministic answers first.** LLM only touches (a) cover letters and (b) novel unknown-type questions → the latter always route to review queue.
- **YOE never from LLM.** Computed at profile-build from the earliest resume mention per skill.
- **3-layer injection defense** (scanner → prompt hardening → output re-scan). The canary-survival test is intentionally scoped to "enclosed" fixtures — for attack kinds where the regex span doesn't cover the canary word, detection alone + review-queue routing is the contract.
- **Private AutoApply repo + combined pipeline + polled approvals** keeps GH Actions usage under the 2000 min/mo cap (~1,990 min/mo projected).
- **Submodule, not copy.** `resumes/` is pinned to a SHA; user bumps with a single commit when recompiling resumes.
- **`DRY_RUN=True` by default** — caller must explicitly opt-out to submit.
- **YOE hard filter: 0–2 years only.** Jobs that explicitly require ≥3 years of experience are hard-rejected (`rejected_by_yoe`). Ambiguous / no-YOE-stated postings are kept. Implemented in `select/yoe_filter.py`; wired into `score` CLI command (2026-04-17). Soft markers ("preferred", "nice to have", etc.) suppress the filter. Sentence-boundary-aware so a prior bullet's soft marker doesn't cancel a hard requirement in the next bullet.

---

## 🛠️ Commands to remember

- Run injection tests: `cd AutoApply && PYTHONPATH=src .venv/bin/python -m pytest tests/test_injection_guard.py -v`
- Run parser tests: `cd AutoApply && PYTHONPATH=src .venv/bin/python -m pytest tests/test_tex_parser.py -v`
- Run answer-bank tests: `cd AutoApply && PYTHONPATH=src .venv/bin/python -m pytest tests/test_answer_bank.py -v`
- Run select tests: `cd AutoApply && PYTHONPATH=src .venv/bin/python -m pytest tests/test_location_filter.py tests/test_pay_extractor.py tests/test_track_picker.py tests/test_dedup.py tests/test_scorer.py tests/test_yoe_filter.py -v`
- Run ingest tests: `cd AutoApply && PYTHONPATH=src .venv/bin/python -m pytest tests/test_ingest.py -v`
- Run tracker tests: `cd AutoApply && PYTHONPATH=src .venv/bin/python -m pytest tests/test_tracker.py -v`
- Run execute tests: `cd AutoApply && PYTHONPATH=src .venv/bin/python -m pytest tests/test_execute.py -v`
- Run review tests: `cd AutoApply && PYTHONPATH=src .venv/bin/python -m pytest tests/test_review.py -v`
- Run congregate tests: `cd AutoApply && PYTHONPATH=src .venv/bin/python -m pytest tests/test_congregate.py -v`
- Run all tests: `cd AutoApply && PYTHONPATH=src .venv/bin/python -m pytest -v` (431 passing as of last update)
- Apply migrations: `cd AutoApply && PYTHONPATH=src .venv/bin/alembic upgrade head`
- Rebuild profile (once CLI exists): `python -m autoapply.cli profile-build`
- Security report (once CLI exists): `python -m autoapply.cli security-report`

## Current test tally: 486 passing (109 injection + 22 parser + 75 answer bank + 122 select + 55 yoe_filter + 17 ingest + 17 tracker + 24 execute + 32 review + 13 congregate)

## 🎉 First Live Dry-Run: SUCCESS (2026-04-17)

**End-to-end pipeline validated with real Greenhouse + Lever APIs:**

```
ingest done — seen=36 inserted=34 skipped=2 (18 GH + 16 Lever)
score done  — ok=24  rej_yoe=10  rej_location=0  rej_injection=0
apply done  — applied=6  reviews=0  failures=0  (dry_run=True)
```

**Additional fixes landed during first run:**
- `track_picker`: broadened SWE/ML title patterns (`developer`, `devops`,
  `cloud engineer`, `systems engineer`, `data scientist`) so FreedomConsulting
  IT roles stopped falling through to track=None
- `standard_fields`: extended machine-key regex to match `resume_text` field;
  added inline placeholder so the Greenhouse textarea-resume field resolves
  without blocking
- `classifier` + `types`: added SECURITY_CLEARANCE_HAVE / SECURITY_CLEARANCE_LEVEL
  types; added "address" patterns to CURRENT_LOCATION rule
- `answer_bank`: security_clearance_have="No", security_clearance_level="None"

**Dry-run payload (Applications Developer / FreedomConsulting / SWE track):**
- All profile fields correct (name, email, phone, LinkedIn, address)
- Resume PDF path → correct swe PDF
- Security clearance → "No" / "None"
- Cover letter generated with SWE track narrative

## Build status: MVP COMPLETE (2026-04-16)

All plan items through Phase 1 are done. Phase 2 (Playwright executor for real submissions)
is the only remaining task. To activate the agent:

1. Push to GitHub, set repo secrets (GEMINI_API_KEY, GH_ISSUES_PAT, SUBMODULE_PAT, AES_KEY_B64)
2. `git submodule update --init resumes/` (or let pipeline.yml do it via SUBMODULE_PAT)
3. Run `autoapply profile-build` once to seed `state/profile.json`
4. Enable the `pipeline` workflow — first run will ingest + score + DRY_RUN apply
5. Review `state/dry_runs/*.json` to confirm form mappings
6. When ready to go live: set `DRY_RUN=false` in repo settings + implement `submit()` in Playwright executor
