# `.claude/` — Developer tooling for agentic coding

This directory is AutoApply's Claude Code configuration. Everything
here makes common dev tasks one keystroke, catches high-cost mistakes
at edit time, and gives Claude context-local invariants when working
in a specific subpackage.

Three audiences read this file:

1. **You (Aadit)** — reference for what commands exist, what hooks
   are running, and how to disable something that's annoying.
2. **Claude** — gets loaded automatically via the settings layer.
3. **Collaborators** — everything here is committed and shared.

Quick links:

- [Slash commands](#slash-commands) — `/test`, `/applied`, `/audit`,
  `/dry`, `/apply`, `/classify`, `/security`, `/presubmit`,
  `/push-safe`, `/test-fast`
- [Hooks](#hooks) — what runs automatically, and how to disable
- [Skills](#skills) — walkthroughs for multi-file tasks
- [Per-directory `CLAUDE.md`](#per-directory-claudemd) — auto-loaded
  context per subpackage

---

## Slash commands

Every command is a single file under `.claude/commands/*.md`. Type
`/<name>` in chat to invoke; `/help` lists them all with descriptions.

### Testing

| Command | Syntax | What it does |
|---------|--------|--------------|
| `/test` | `/test` or `/test <pattern>` | Run full pytest (~1.2s) or `-k <pattern>` filtered subset |
| `/test-fast` | `/test-fast` | Injection guard + rules loader + parsers + audit (<1s). Use when iterating on rules / prompts / parsers |
| `/push-safe` | `/push-safe` | Runs full pytest; if green, pushes `HEAD` to origin. Blocks on red tests |

### Applications (submission lifecycle)

| Command | Syntax | What it does |
|---------|--------|--------------|
| `/dry` | `/dry <JOB_ID> [JOB_ID ...]` | Dry-run through the full resolver (classifier + batch LLM + audit). Does NOT open Playwright. Payload dumps to `state/dry_runs/` |
| `/apply` | `/apply <JOB_ID> [JOB_ID ...]` | **REAL SUBMIT** — opens Playwright, uploads, fills, clicks submit. Prints a duplicate-risk warning banner first |
| `/applied` | `/applied [N]` | Last `N` applications (default 10) from `state/jobs.sqlite` — id, company, outcome, submit time, batch model |
| `/audit` | `/audit <APP_ID>` | Full post-mortem for one application — batch audit, cascade trace, per-field sources |
| `/presubmit` | `/presubmit <APP_ID>` | Open the pre-submit screenshot for an application (shows React-Select state at click time) |

### Debugging

| Command | Syntax | What it does |
|---------|--------|--------------|
| `/classify` | `/classify <question label>` | Run a raw label through the classifier; prints `QuestionType`, confidence, slot, source |
| `/security` | `/security [days]` | `SecurityEvent` rows (injection attempts, etc.) from the last `N` days (default 7) |

### Adding a new command

Copy an existing file under `.claude/commands/` and edit the
frontmatter. The `description` shows in `/help`. Argument access:

- `$ARGUMENTS` — all args as one string
- `$1`, `$2`, … — positional
- Inline shell: `` ```! ... ``` `` block

Re-reads every invocation — no restart needed.

---

## Hooks

Five hooks wired in `.claude/settings.json`. All scripts live under
`.claude/hooks/`. Each fails soft (exit 0 even on error) unless it's
a `block-destructive` hit.

### Auto-run on edit

**`ruff-fix.sh`** (PostToolUse on `Write|Edit`) — formats + lints
any `.py` file under `src/` or `tests/` using the same ruff config
CI uses (`pyproject.toml:51-57`). Silent on success. No-ops on
non-Python files.

**`rules-smoke.sh`** (PostToolUse on `Write|Edit`) — runs
`tests/test_rules_loader.py -q` after any edit to `state/rules/*.yml`
or `prompts/*.md`. Surfaces schema regressions (rename / typo /
shape change) at edit time. Runs in ~0.2s.

### Auto-block dangerous bash

**`block-destructive.sh`** (PreToolUse on `Bash`) — blocks the
catastrophic commands:

- `rm -rf state/jobs.sqlite` (18MB of live application history)
- `rm -rf resumes` (private submodule)
- `git push --force` to main/master
- `git reset --hard origin/*` (discards local commits)
- `alembic downgrade`
- Bulk `DROP TABLE` / `DELETE FROM applications` via sqlite3

Low-risk `rm -rf` on other paths is not touched — over-policing
creates more friction than it prevents.

### Context injection at prompt time

**`context-dump.sh`** (UserPromptSubmit) — emits one line of repo
state (`[repo] branch=main modified=2 untracked=1 ahead=0 behind=0`)
so Claude knows if there are uncommitted edits without re-asking.
Minimal output.

**`skill-hint.sh`** (UserPromptSubmit) — pattern-matches the user
prompt against `.claude/hooks/skill-hints.yml`. When a known phrase
fires ("add a question type", "react-select", "prompt", etc.),
injects a one-line pointer at the relevant skill or per-dir
`CLAUDE.md`. Silent on no-match.

### Disabling a hook

Per-session: put this in `.claude/settings.local.json` (gitignored):

```json
{"disableAllHooks": true}
```

Or, to disable just one hook, remove its entry from
`.claude/settings.json` temporarily (but commit-git revert when
you're done so the hook stays wired for the team / future you).

### Editing a skill hint

`.claude/hooks/skill-hints.yml` is the dispatch table. One-line YAML
edit adds a new pattern → hint pair. No bash changes.

```yaml
- pattern: '(your|regex|here)'
  hint: 'One-line pointer at .claude/skills/... or src/.../CLAUDE.md'
```

---

## Skills

Under `.claude/skills/<name>/SKILL.md`. Skills auto-surface via the
`when_to_use` frontmatter OR via the `skill-hint.sh` hook when the
user prompt matches a configured phrase.

| Skill | Fires when | Walks through |
|-------|------------|---------------|
| [`add-question-type`](skills/add-question-type/SKILL.md) | "add a question type", "new QuestionType", "PROFILE_SOURCED", "LLM_REQUIRED" | 7-step checklist across `answers/types.py`, `classifier.py`, `bank.py`, `profile/schema.py`, `state/profile.json`, `tests/test_answer_bank.py` |
| [`add-state-rule`](skills/add-state-rule/SKILL.md) | "add a rule yaml", "new state/rules" | 3-step checklist: YAML with header comment, consumer via `load_rules(name)`, smoke test in `test_rules_loader.py` |

### Adding a new skill

Create `.claude/skills/<name>/SKILL.md` with frontmatter:

```yaml
---
name: my-skill
description: One-line what it does
when_to_use: Natural-language phrase describing when to fire
---
```

Optionally add a matching pattern to `.claude/hooks/skill-hints.yml`
so the hook catches tangentially-phrased asks that auto-discovery
would miss.

---

## Per-directory `CLAUDE.md`

Subpackage-specific context, auto-loaded when Claude opens a file
under that directory. Each is 40-80 lines — just the invariants
local to that package, no re-statement of the top-level `CLAUDE.md`.

| Path | Covers |
|------|--------|
| `src/autoapply/execute/submitter/CLAUDE.md` | Phase order, React-Select gotchas, backcompat shim rules |
| `src/autoapply/execute/resolution/CLAUDE.md` | 4-phase contract, never-batch-machine-key, leave-optional-blank |
| `src/autoapply/answers/CLAUDE.md` | Classifier regex ordering, PROFILE_SOURCED flow, cascade invariants |
| `src/autoapply/select/CLAUDE.md` | Hard filters vs. soft signals, track-picker has no firm list |
| `state/rules/CLAUDE.md` | New YAML needs a smoke-test entry, loader caches per-process |
| `prompts/CLAUDE.md` | Placeholder contract for `batch.md` + `batch_rules.md`, UNTRUSTED wrapping |
| `tests/CLAUDE.md` | Hermetic policy, `_stub_cascade_factory` pattern for LLM tests |

---

## Top-level `settings.json`

```json
{
  "permissions": { "deny": [...] },
  "hooks": {
    "PostToolUse": [{ "matcher": "Write|Edit", "hooks": [ruff-fix, rules-smoke] }],
    "PreToolUse":  [{ "matcher": "Bash",        "hooks": [block-destructive] }],
    "UserPromptSubmit": [{ "hooks": [context-dump, skill-hint] }]
  }
}
```

Committed to git. For personal overrides (disabling hooks, changing
env vars, etc.) use `.claude/settings.local.json` (gitignored).

---

## Philosophy

- **Hooks fail soft.** A broken hook should never block your work.
  Only `block-destructive.sh` exits non-zero, and only for the
  genuinely catastrophic cases.
- **Data-driven where possible.** Rules, hints, patterns → YAML.
  Editing policy doesn't require touching bash.
- **Small, readable, composable.** Every hook script, slash command,
  and skill file is <100 LOC. No clever frameworks.
- **The repo is the source of truth.** No hidden personal config
  that only works on your machine.
