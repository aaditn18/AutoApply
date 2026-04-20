---
description: "Run pytest (full suite or -k filtered subset)"
argument-hint: "[pattern]"
allowed-tools: "Bash(python *), Bash(pytest *)"
---

Run the AutoApply test suite.

- No argument → full suite (~1.2 s, 720 tests expected to pass).
- With argument → pass it as `-k <pattern>` (pytest expression syntax,
  matches test names).

```!
if [ -z "$ARGUMENTS" ]; then
  python -m pytest -q 2>&1 | tail -8
else
  python -m pytest -q -k "$ARGUMENTS" 2>&1 | tail -12
fi
```

Report the result concisely: the test count, passing/failing status,
and any failure names. If anything fails, fetch the relevant traceback
with `python -m pytest tests/test_<name>.py::<test> -v`.
