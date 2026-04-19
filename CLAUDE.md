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
- **One LLM call per application**, not per-field. See
  `src/autoapply/answers/llm_batch.py`. Model cascade:
  `gemini-3.1-flash-lite → gemini-2.5-flash-lite → gemini-3-flash →
  gemini-2.5-flash` (first 3 often 404 on v1 stable; 2.5-flash-lite is
  the workhorse). Use `api_version="v1"` — **`gemini-2.0-flash` is
  deprecated** (removed from free tier March 2026).
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

## Recent architecture (2026-04-19)

- **Resolver pipeline** is two phases in
  `src/autoapply/execute/standard_fields.py::resolve_all_batched`:
  - Phase 1 — classifier + Profile / bank (free, deterministic)
  - Phase 2 — ONE batched Gemini call with JD + remaining questions
- **Submitter** is split across `src/autoapply/execute/submitter/`
  (was a 2142-LOC file). `driver.py` is the orchestrator; other modules
  are leaf helpers.
- **Stage-2 DOM batch** (`submitter/dom_batch.py`) — after resume upload
  + SPA re-render, scrape visible required fields and batch-LLM them.
  Needed for Greenhouse's new `job-boards.greenhouse.io` SPA which
  injects fields not in the API `/questions` endpoint. Includes a
  "late-rescan" pass for EEO fields that only appear once others fill.
- **Education preference matching** — School/Degree/Major dropdowns on
  multi-option tenants (UMD has 4 campuses; "Bachelor's Degree" vs
  "B.S." vs "Bachelor of Science") use ordered regex preference lists
  in `submitter/dom_batch.py::_SCHOOL_OPTION_PREFERENCES` etc.
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
python -m pytest -q           # must print "626 passed" (plus any you added)

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
