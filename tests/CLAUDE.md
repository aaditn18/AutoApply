# tests/ — hermetic policy + test-writing conventions

## Hermetic policy (`conftest.py`)

**Autouse fixture `_block_live_gemini` blocks every test from hitting
the live Gemini API.** Two layers:

1. Monkeypatches `autoapply.answers.llm_batch._call_with_cascade` to
   return a `BatchResult(error="live-LLM call blocked by test fixture")`.
2. Monkeypatches `autoapply.answers.llm_fallback._call_gemini` to
   return `None`.
3. Clears `GEMINI_API_KEY` from `os.environ` AND from the cached
   `get_settings()` result.

If you see `"live-LLM call blocked by test fixture"` in test output,
the test reached an LLM path without stubbing. Fix the test, not the
fixture.

## Stubbing the LLM for tests that need it

Tests that want to exercise the batched-LLM flow monkeypatch
`_call_with_cascade` back with a scripted stub. The pattern:

```python
def _stub_cascade(prompt, api_key, questions):
    from autoapply.answers.llm_batch import BatchAnswer, BatchResult
    return BatchResult(
        answers={
            q.id: BatchAnswer(
                question_id=q.id,
                value="Yes",
                source="llm_reasoning",
                confidence=0.9,
            )
            for q in questions
        },
        model_used="gemini-2.5-flash-lite",
        cascade_trace=[{"model": "gemini-2.5-flash-lite", "ok": True}],
    )

monkeypatch.setattr(
    "autoapply.answers.llm_batch._call_with_cascade", _stub_cascade,
)
```

`tests/test_llm_batch.py::_stub_cascade_factory` is the reference.

## File organization

Every subpackage has its own test file:

| Subpackage | Test file |
|------------|-----------|
| `answers/classifier + bank` | `test_answer_bank.py` |
| `answers/llm_batch` | `test_llm_batch.py` |
| `answers/batch_prompt + batch_parse + adapters/gemini` | `test_batch_split.py` |
| `execute/submitter/dom/` | `test_dom_package.py` |
| `execute/submitter/fillers/` | `test_fillers_package.py` |
| `execute/submitter/phases/` | `test_driver_phases.py` |
| `execute/resolution/` | `test_resolution_package.py` |
| `execute/audit + review_flags` | `test_audit_review_flags.py` |
| `state/rules + prompts` | `test_rules_loader.py` |

## Invariants

- **Never hit the real API.** Even when `GEMINI_API_KEY` is set in
  the environment; the fixture clears it. If you want to test against
  the live API, do so manually outside pytest.
- **No test opens a real browser.** Playwright-bound code (submitter/
  phases, dom/, fillers/) is tested via stubs (see `_StubPage`,
  `_StubEl` patterns in `test_dom_package.py` / `test_fillers_package.py`).
- **Backcompat identity checks** are critical after a refactor — see
  `test_X_package.py` files for the `module.X is shim.X` pattern that
  catches duplicate implementations sneaking in.
- **Full suite runs in ~1.2s.** If you write a test that takes more
  than 0.5s, there's almost certainly an uncaught live call or a real
  Playwright launch. Investigate before merging.

## Run

- Full suite: `/test`
- Fast subset: `/test-fast` (rules loader + injection guard + parsers)
- Single test: `/test test_name::test_specific`
