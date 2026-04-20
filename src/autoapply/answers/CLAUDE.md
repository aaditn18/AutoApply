# Answers subpackage — classifier + bank + batch LLM

## Module layout

- `types.py` — `QuestionType` enum + 3 policy frozensets
  (`PROFILE_SOURCED`, `LLM_REQUIRED`, `REVIEW_REQUIRED`).
- `classifier.py` — 140-regex rule table (specific-first) +
  skill-alias canonicalization from `state/rules/skill_aliases.yml`.
- `bank.py` — `AnswerBank` + `_from_profile` (dict-driven lookups
  for 30+ PROFILE_SOURCED types, plus hand-written handlers for
  names / education / YOE).
- `llm_batch.py` — public facade: `BatchQuestion`, `BatchAnswer`,
  `BatchResult`, `resolve_batch`. Backcompat re-exports for
  `_call_with_cascade`, `_parse_response`, `_build_prompt`,
  `_validate_select_value`.
- `batch_prompt.py` — `build_prompt` + `profile_as_json`. Consumes
  `prompts/batch.md` + `prompts/batch_rules.md`.
- `batch_parse.py` — `parse_response` + option validation. Strips
  markdown fences, gates on confidence ≥ 0.5.
- `llm_fallback.py` — per-field Gemini fallback (select-only now;
  text/textarea defers to the batch for JD context).

## Classifier invariants

- **Regex order matters.** Earlier entries in `_RULES` win. Put
  specific patterns first; generic catch-alls last. Example: the
  `YOE_LANGUAGE` rule must come before the generic `YEARS` rule.
- **Skill aliases live in YAML.** `state/rules/skill_aliases.yml`.
  Editing the canonical form doesn't require a code change.
- **UNKNOWN is a signal, not a failure.** Returning UNKNOWN tells
  the caller "route to LLM or review" — not "classifier is broken".
- **Normalization strips `*` and `(required)` markers** from labels
  so anchored rules (`^city$`) fire on `"City*"`.

## PROFILE_SOURCED flow

Adding a new PROFILE_SOURCED type requires 4 touches:

1. Add the enum value to `types.py::QuestionType`.
2. Add it to `types.py::PROFILE_SOURCED` frozenset.
3. Add a regex rule to `classifier.py::_RULES`.
4. Map it in `bank.py::_SIMPLE_PROFILE_ATTRS` dict (or add a
   handler to `_from_profile` if non-trivial).
5. If the Profile field doesn't exist yet, add it to
   `profile/schema.py` AND populate in `state/profile.json` for
   each track.
6. Add a paraphrase test in `tests/test_answer_bank.py`.

The `add-question-type` skill (`.claude/skills/add-question-type/`)
walks through this.

## Batch LLM invariants

- **One Gemini call per application, not per-field.** Amortizes
  prompt overhead + lets the LLM see all fields for cross-field
  reasoning.
- **Cascade order:** `gemini-3.1-flash-lite → 2.5-flash-lite →
  3-flash → 2.5-flash`. Defined in `adapters/gemini.py::MODEL_CASCADE`.
- **NEVER re-add `gemini-2.0-flash`.** Deprecated March 2026,
  removed from free tier — 404s on v1.
- **Every prompt wraps untrusted input in `<UNTRUSTED>` tags.**
  JD in one block, scraped questions in another. Re-scan the
  output for canary leaks (`security/injection_guard`).
- **`confidence < 0.5` → force `needs_review`.** Low-confidence
  answers route to human review, not submitted.
- **YOE / EEO / citizenship / visa are NEVER LLM-answered.** All
  PROFILE_SOURCED.

## Prompts

Live in `prompts/*.md`, loaded via `autoapply.rules.load_prompt(name)`.
See `prompts/CLAUDE.md` for the placeholder contract.

## Tests

- `tests/test_answer_bank.py` — 250+ paraphrase/negative cases for
  the classifier + bank.
- `tests/test_llm_batch.py` — 32 cascade + parser tests.
- `tests/test_batch_split.py` — 17 tests for the Phase-6 split.
- `tests/test_audit_review_flags.py` — `_from_profile` regression suite.
