# CLAUDE.md — onboarding for future Claude sessions

This file is the first thing to read when Claude is invoked on this repo.
It tells you where the source of truth lives and what the non-obvious
invariants are. **Read [`README.md`](./README.md) first for setup /
architecture; read [`log.md`](./log.md) second for the last decisions
made and why.**

---

## What this repo is

Autonomous job-application agent. Applies to Greenhouse + (planned)
Lever / YC / Handshake postings on behalf of Aadit Nilay (UMD CS+Math,
new grad May 2026, GPA 3.975, F-1 OPT with STEM extension through 2029).
Runs on GitHub Actions within a ~2000 min/mo + $0 LLM free-tier budget.

4 resume tracks (SWE / ML / HPC / Quant) compiled from LaTeX files in
a private submodule; track is picked from job content, never from a
firm list.

---

## Reading order

1. **[README.md](./README.md)** — architecture + setup + directory layout
   + configuration + CLI. If you need to edit something, the section
   headings in the directory layout tell you which file.
2. **[log.md](./log.md)** — chronological work log with the **rationale**
   behind the shape of the code. Newest entries at the bottom; historical
   milestones summary at the very end.
3. **[.claude/plans/drifting-snuggling-harbor.md](./.claude/plans/drifting-snuggling-harbor.md)** —
   the original big-picture plan. Most of it is implemented; a few
   phase-3 items are still pending.

When in doubt, search `log.md` for the feature/bug name first — odds are
there's already a paragraph explaining why it's the way it is.

---

## Core invariants (don't break these without explicit reason)

- **`DRY_RUN=True` is the default.** `--no-dry-run` for real submissions.
- **Deterministic answers first, LLM as fallback.** Classifier + Profile +
  answer_bank covers the vast majority of fields. Only fields the
  classifier can't answer confidently (novel essays, dropdowns with
  non-matching options, SPA-injected fields) go to the batched LLM.
- **Type-mismatch on select fields defers to the LLM.** When the Stage-2
  pre-resolve produces a strictly-numeric value (profile GPA `"3.975"`)
  for a select whose options contain no digits (`["Yes", "No"]`), the
  field is routed to the batched LLM so the threshold rule in
  `prompts/batch_rules.md` can fire. Text-vs-text mismatches (school
  async-typeaheads) keep the type-and-filter path — see
  `execute/submitter/dom/resolve.py`.
- **Location filter is a strict US-whitelist.** Non-US country denylists
  can never be exhaustive (Bulgaria / Greece / Croatia etc. kept slipping
  through). `is_us_location` accepts only strings matching
  `_US_ACCEPT_REGEX` — US country variants, 50 state names + DC + PR,
  or 2-letter state abbrevs — matched word-bounded anywhere. Bare
  `"Remote"` is rejected.
- **One LLM call per application**, not per-field. Public entry point
  lives in `src/autoapply/answers/llm_batch.py`; implementation is
  split: `answers/batch_prompt.py` (prompt construction), `answers/batch_parse.py`
  (response validation), `adapters/gemini.py` (SDK + model cascade).
  Cascade order: `gemini-3.1-flash-lite → gemini-2.5-flash-lite →
  gemini-3-flash → gemini-2.5-flash` (first 3 often 404 on v1 stable;
  2.5-flash-lite is the workhorse). Use `api_version="v1"` —
  **`gemini-2.0-flash` is deprecated** (removed from free tier March 2026).
- **Every LLM prompt wraps untrusted input in `<UNTRUSTED>` tags**
  (JD and scraped question list each in their own block). Output is
  re-scanned for canary leaks. See `src/autoapply/security/`.
- **YOE is never LLM-answered.** Computed at profile-build time from
  resume dates. Same for EEO / citizenship / visa — those live on
  `Profile` now and are `PROFILE_SOURCED`.
- **Hermetic tests.** `tests/conftest.py` autouse-fixtures block every
  live Gemini call. If a test needs an LLM response, script a stub on
  `_call_with_cascade` or `_call_gemini`.
- **Co-Authored-By footer** on every commit:
  `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`

---

## `.claude/` tooling (2026-04-20)

This repo has a full agentic-coding layer under `.claude/`. See
[`.claude/README.md`](./.claude/README.md) for the user-facing index.

- **Slash commands** (type in chat): `/test`, `/test-fast`,
  `/applied`, `/audit`, `/dry`, `/apply`, `/classify`, `/security`,
  `/presubmit`, `/push-safe`.
- **Hooks** that fire automatically: ruff format on `.py` edits,
  schema-smoke on YAML/prompt edits, destructive-bash blocker
  (`rm -rf state/jobs.sqlite`, force push, alembic downgrade),
  skill-hint injection on prompt submit, docs-sync warning at
  commit/push time.
- **Per-directory `CLAUDE.md`** auto-loads when editing a file under
  `execute/submitter/`, `execute/resolution/`, `answers/`, `select/`,
  `state/rules/`, `prompts/`, or `tests/` — each holds local
  invariants so you don't need to re-read this file.
- **Skills** (auto-surfaced): `add-question-type` and
  `add-state-rule` walk through the multi-file checklists.
- **Data-driven dispatch**: `.claude/hooks/skill-hints.yml` and
  `.claude/hooks/docs-sync-map.yml` are edited without touching bash.

## Refactor state (7 phases complete, 2026-04-19)

Large-scale structural refactor shipped in 7 commits. Net: every
previously-monolithic hot-spot module split into cohesive subpackages
with isolated concerns. 626 → 753 tests (+127, incl. 2026-04-21
classifier/filter gaps and the checkbox-group dispatch fix).
Behavior-preserving — no functional changes. See `log.md` sections
U–Z' for per-phase rationale and the final summary table.

Key post-refactor landmarks:
- `state/rules/*.yml` + `prompts/*.md` — all policy rules as data.
- `execute/submitter/phases/` — one module per submission phase.
- `execute/submitter/dom/` — Stage-2 DOM pipeline (7 modules).
- `execute/submitter/fillers/` — per-strategy field fillers.
- `execute/resolution/` — two-phase resolver (6 modules).
- `adapters/gemini.py` — IO-boundary layer; the only google-genai import.
- `answers/batch_prompt.py` + `answers/batch_parse.py` — prompt + parse split from llm_batch.
- `execute/audit.py` + `execute/review_flags.py` — extracted from base.apply.

## Policy layer — rules as data (2026-04-19 refactor, Phase 1)

Business rules live as **data files**, not Python constants:

- `state/rules/*.yml` — YAML rule tables (education preferences, skill
  aliases, machine-key patterns, geography, EEO semantics, export-control
  markers, browser UA pool).
- `prompts/*.md` — LLM prompt templates (`batch.md`, `batch_rules.md`).

Loader: `autoapply.rules.load_rules(name)` and `load_prompt(name)`.
Both are `@lru_cache`d per-process. Adding a new education-preference
pattern or skill alias is a YAML edit, not a code change.

When you add a new rule file, **also add a schema smoke-test** in
`tests/test_rules_loader.py` — consumers assume a specific top-level
shape, and a typo in a rename would fail silently at apply time.

## Recent architecture (2026-04-19)

- **Resolver pipeline** lives under `src/autoapply/execute/resolution/`
  (split from the old `standard_fields.py` monolith; that module is
  now a thin facade exposing the dataclasses + public functions):
  - Phase 1 — `resolution/phase1.py`: classifier + Profile / bank
    (free, deterministic).
  - Phase 2 — `resolution/batch_builder.py`: decide what needs the LLM.
  - Phase 3 — `answers/llm_batch.py::resolve_batch`: ONE batched
    Gemini call with JD + remaining questions.
  - Phase 4 — `resolution/backfill.py`: apply answers onto Phase-1 state.
  The top-level composition is `resolution/orchestrator.py::resolve_all_batched`.
- **Submitter** is split across `src/autoapply/execute/submitter/`
  (was a 2142-LOC file). `driver.py` is the orchestrator composing
  phases from `submitter/phases/` (browser, upload, api_fill, stage2,
  submit_click, verification, verify). Other siblings
  (field_fill, lever_cards, captcha_*, etc.) are leaf helpers.
- **Stage-2 DOM batch** lives under `submitter/dom/` (package split
  from the former 1200-LOC `dom_batch.py` — now a re-export shim).
  After resume upload + SPA re-render, `dom/batch.py::batch_resolve_dom_fields`
  scrapes visible required fields and batch-LLM's them. Needed for
  Greenhouse's new `job-boards.greenhouse.io` SPA which injects fields
  not in the API `/questions` endpoint. Includes a "late-rescan" pass
  for EEO fields that only appear once others fill.
- **Education preference matching** — School/Degree/Major dropdowns on
  multi-option tenants (UMD has 4 campuses; "Bachelor's Degree" vs
  "B.S." vs "Bachelor of Science") use ordered regex preference lists
  under `submitter/dom/preferences.py` (loaded from
  `state/rules/education_preferences.yml`).
  `fill_combobox(prefer_patterns=...)` applies these before the
  alphabetical-first prefix match.
- **`resume_text` field**: plain-text extracted from the `.tex` source
  (same file the PDF compiles from). See `profile/tex_to_text.py`. No
  LLM round-trip.
- **Common-across-tracks fields on Profile** (EEO, citizenship, visa,
  willing-states) — not in the bank anymore. Update
  `state/profile.json` + `src/autoapply/profile/schema.py` together.

---

## How to verify a change

```bash
# 1. Tests
python -m pytest -q           # must print "719 passed" (plus any you added)

# 2. Dry-run on a single job to see the resolution pipeline
python scripts/apply_by_job_ids.py <JOB_ID>

# 3. Real submit only after you're sure
python scripts/apply_by_job_ids.py <JOB_ID> --no-dry-run

# 4. DB audit — Application.artifacts.batch_audit shows per-field source
sqlite3 state/jobs.sqlite "SELECT outcome, artifacts FROM applications
                          ORDER BY submitted_at DESC LIMIT 3"
```

---

## Things not to do

- Don't re-add `gemini-2.0-flash` anywhere. It 404s on free tier.
- Don't re-add the naive binary Yes/No fallback in `fill_combobox`
  (it was removed because it picked "Yes" for "GPA of 4.0+?" with a
  3.975 GPA).
- Don't hardcode company names into the track picker. There is no
  "this firm is quant" table by design.
- Don't make the batch LLM fill `resume_text` — it pastes the whole
  resume into fields the form ignores. The `.tex` → plaintext path
  handles this deterministically.
- Don't skip the `<UNTRUSTED>` wrapping for any prompt that includes
  scraped web content.
- Don't re-add business rules as Python constants. Education
  preferences, skill aliases, EEO decline phrasings, UA pool, prompt
  text — all live under `state/rules/` or `prompts/` and load via
  `autoapply.rules`. Adding inline `_DECLINE_KEYWORDS = (...)` in a
  module is a regression of the Phase-1 refactor.
