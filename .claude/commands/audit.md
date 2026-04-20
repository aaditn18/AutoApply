---
description: "Dump batch_audit + resolved fields for a specific Application.id"
argument-hint: "<APP_ID>"
allowed-tools: "Bash(.claude/bin/audit.sh:*)"
---

Full post-mortem for one application: job metadata + batch LLM audit
(model, answer count, cascade trace) + any `needs_review` entries.

```!
.claude/bin/audit.sh $ARGUMENTS
```

Report outcome, model answered, questions batched, cascade fallback
trace.
