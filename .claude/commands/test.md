---
description: "Run pytest (full suite or -k filtered subset)"
argument-hint: "[pattern]"
allowed-tools: "Bash(.claude/bin/test.sh:*)"
---

Run the AutoApply test suite.

- No argument → full suite (~1.2s, 720 tests).
- With argument → pytest `-k` expression filter.

```!
.claude/bin/test.sh $ARGUMENTS
```

Report pass/fail count and any failure names. On failure, fetch the
traceback with `python -m pytest tests/test_<name>.py::<test> -v`.
