---
description: "Run the fast test subset (injection guard + rules loader + parsers + audit, <1s)"
allowed-tools: "Bash(.claude/bin/test-fast.sh:*)"
---

Fast-feedback subset covering the highest-churn areas:

- `test_injection_guard.py` — merge-blocking security fixtures.
- `test_rules_loader.py` — YAML schema smoke.
- `test_tex_parser.py` — resume parsing.
- `test_audit_review_flags.py` — base.apply extraction sanity.

```!
.claude/bin/test-fast.sh
```

Run `/test` if the fast subset passes but you suspect something broader.
