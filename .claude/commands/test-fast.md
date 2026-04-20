---
description: "Run the fast test subset (injection guard + rules loader + core parsers, <1s)"
allowed-tools: "Bash(python *)"
---

Run the minimal fast-feedback subset:

- `tests/test_injection_guard.py` — merge-blocking security fixtures
- `tests/test_rules_loader.py` — YAML schema smoke
- `tests/test_tex_parser.py` — resume parsing
- `tests/test_audit_review_flags.py` — base.apply extraction sanity

Use this instead of `/test` when iterating on classifier rules,
prompts, or YAML policy — it catches the highest-likelihood
regressions for those edit targets in under a second.

```!
python -m pytest \
  tests/test_injection_guard.py \
  tests/test_rules_loader.py \
  tests/test_tex_parser.py \
  tests/test_audit_review_flags.py \
  -q 2>&1 | tail -6
```

Report pass/fail count. Run the full `/test` if the fast subset
passes but you suspect something broader.
