# prompts/ — LLM prompt templates

Plain markdown. Loaded via `autoapply.rules.load_prompt(name)` at
import time (cached per-process).

## Files

- **`batch.md`** — the outer prompt template for the batched resolver.
  Wraps the untrusted content in `<UNTRUSTED>` tags.
- **`batch_rules.md`** — the 12 numbered CORE RULES the LLM must
  follow (never invent facts, threshold gates, work-auth defaults,
  EEO decline, essay tailoring, option-match exactness, etc.).

## Placeholder contract

`batch.md` uses these `{placeholder}` fields (str.format-style):

- `{rules}` — `batch_rules.md` content, pre-formatted with `{track}`.
- `{meta_block}` — `Track: swe\nCompany: FooCorp\nRole: SWE I`
- `{profile_block}` — profile JSON (compact, trimmed to 3000 chars)
- `{bank_block}` — `state/answer_bank.yml` raw YAML (trimmed to 4000)
- `{jd_header}` — ` for "Company / Role"` (or empty)
- `{jd_body}` — sanitized JD (trimmed to 4000)
- `{questions_json}` — sanitized question list as JSON string

`batch_rules.md` uses `{track}` only (formatted first, before
`batch.md`).

**Double-brace literals:** `{{` / `}}` in `batch.md` render as
`{` / `}`. Used in the output-schema JSON example. Don't lose them
when editing.

## Invariants

- **Every untrusted input wraps in `<UNTRUSTED>` tags.** JD in one
  block, scraped questions in another. Non-negotiable — this is the
  primary injection defense.
- **Output-format block must stay explicit.** The LLM's JSON must
  have `{"answers": [{"id": ..., "value": ..., "source": ..., "confidence": ..., "reasoning": ...}]}`.
  `batch_parse.py` hard-requires this shape.
- **Rules file has a `{track}` placeholder.** Consumer pre-formats
  with `track=swe/ml/hpc/quant` before embedding in the outer prompt.
- **`source` field is an enum:** `llm_reasoning | llm_generation |
  llm_option_match | needs_review`. Don't introduce new values without
  updating `execute/audit.py::bucket_source`.

## Editing a prompt

Touching `batch.md` or `batch_rules.md` fires the `rules-smoke.sh`
PostToolUse hook (`tests/test_rules_loader.py` verifies placeholder
presence + `.format()` cleanness). Break the placeholder contract
and you see the test fail at edit time.

## Tests

- `tests/test_rules_loader.py::test_batch_prompt_template_has_required_placeholders`
- `tests/test_rules_loader.py::test_batch_prompt_formats_without_error`
- `tests/test_rules_loader.py::test_batch_rules_prompt_has_all_numbered_rules`
- `tests/test_batch_split.py::test_build_prompt_wraps_job_description_in_untrusted`
- `tests/test_batch_split.py::test_build_prompt_sanitizes_question_labels`
