# Resolution subpackage — two-phase field resolver

## 4-phase contract

```
Phase 1 (phase1.py)        — deterministic: machine_key → classifier → bank → per-field LLM (select-only)
Phase 2 (batch_builder.py) — decide which required + unconfident fields need the LLM
Phase 3 (llm_batch)        — ONE Gemini call with JD + all remaining questions
Phase 4 (backfill.py)      — apply LLM answers onto Phase-1 state; promote unresolved → resolved
```

Composed by `orchestrator.py::resolve_all_batched`, which is the only
public entry point from this package. `standard_fields.py` re-exports
it + the dataclasses for callers.

## Key invariants

- **Machine-key resolved fields NEVER batch.** `batch_builder.build_batch`
  skips `r.source == "machine_key"` even when the value is empty
  (e.g., deliberately blank `resume_text`). Batching them causes the
  LLM to paste the entire resume into a field the form ignores.
- **Optional unresolved fields left BLANK by design.** We don't burn
  LLM tokens on optional pronouns / optional "how did you hear".
  Only required + unconfident fields go into the batch.
- **`source="needs_review"` from the LLM preserves Phase-1 state.**
  Unresolved stays unresolved. The review queue catches these.
- **Substring option matches don't count.** `_value_matches_option`
  requires exact case-insensitive equality. React-Select rejects
  substrings during post-submit validation.

## When to add a new source

If you introduce a new `ResolvedField.source` value (beyond the
existing `machine_key / profile / bank / classifier+bank / llm_required
/ review_required / none / llm_answer / llm_batch:<sub>`), update:

1. `execute/audit.py::bucket_source` — map it to a display bucket.
2. `tests/test_audit_review_flags.py::test_bucket_source_maps_core_sources`
   — add the new source.

## Files

- `machine_key.py` — regex table from `state/rules/machine_keys.yml`
- `options_snap.py` — ITAR/EAR fallback from `state/rules/export_control.yml`
- `phase1.py` — `resolve_field` + `resolve_all`
- `batch_builder.py` — `build_batch` + `_value_matches_option`
- `backfill.py` — `apply_answers` + `_serialize_answer_value`
- `orchestrator.py` — `resolve_all_batched`

## Tests

`tests/test_resolution_package.py` — 11 tests covering backcompat
identity, batch-builder's skip-machine_key + leave-optional-blank
rules, backfill's promotion + needs_review preservation.
