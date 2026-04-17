# AutoApply

Autonomous job-application agent for Aadit Nilay. Ingests jobs from Greenhouse/Lever (and later YC WAAS, Handshake, Simplify, Otta), ranks by fit + pay + location, picks the right resume track (SWE / ML / HPC / Quant), and applies on a tiered auto-vs-review basis — all driven by GitHub Actions on a private repo with a $0–$6/mo budget.

## Quickstart (local dev)

```bash
cd "Downloads/Resume Stuff/AutoApply"
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
playwright install chromium
cp .env.example .env        # fill in API keys
autoapply profile-build     # parse resume .tex files -> state/profile.json
autoapply ingest            # pull latest GH/Lever jobs
autoapply score             # rank + pick track
DRY_RUN=true autoapply apply --limit 5   # fill forms, do NOT submit
```

## Safety defaults

- `DRY_RUN=true` is the default. The first real submission only happens when `DRY_RUN=false` is explicitly set AND the app is either `queued_auto` with all-cache-hit answers OR human-approved via `/approve` in a GH Issue.
- Every job description is scanned for prompt-injection attempts before any LLM call. Detections → review queue or reject.
- YOE answers are computed deterministically from the parsed profile; the LLM never answers "years of experience" questions.
- Hard filters: non-US location → reject; >1 application per company per 60 days → reject.

## Design

See [the plan](../../.claude/plans/drifting-snuggling-harbor.md) for the full architecture doc.

## Layout

```
src/autoapply/
  security/    prompt-injection guard + sanitizer
  profile/     .tex → profile.json (deterministic parser)
  answers/     (question_type, track) → deterministic answer; LLM only for novel
  ingest/      Greenhouse, Lever, (later) YC, Handshake, Simplify, Otta
  select/      dedup, location_filter, pay_extractor, scorer, track_picker
  congregate/  per-job payload + optional cover letter
  execute/     HTTP fast path + Playwright fallback
  review/      GH Issues as review queue; /approve listener
  tracker/     SQLAlchemy models (Job, Application, Event, SecurityEvent)
state/         profile.json, answer_bank.yml, jobs.sqlite (committed)
prompts/       LLM prompts as .md (injection-guarded)
tests/         pytest; injection-guard suite is merge-blocking
```

## Operations

Four GitHub Actions workflows:
- `pipeline.yml` — cron every 6h: ingest → score → apply. Commits state back.
- `review-listener.yml` — on issue_comment: submits a single job when Aadit comments `/approve` (or `/approve --track=ml`).
- `nightly.yml` — 05:00 UTC: DB vacuum, archive, profile rebuild, security report, daily digest.
- `tests.yml` — on PR: runs the full pytest suite including the 30-fixture injection-guard test (merge-blocking).
