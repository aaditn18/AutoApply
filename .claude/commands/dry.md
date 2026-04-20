---
description: "Dry-run apply to one or more Job.id — wraps scripts/apply_by_job_ids.py"
argument-hint: "<JOB_ID> [JOB_ID ...]"
allowed-tools: "Bash(.claude/bin/dry.sh:*)"
---

Dry-run one or more jobs through the full resolution pipeline
(classifier + batch LLM + audit). Does NOT open Playwright — the
resolver dumps the payload to `state/dry_runs/` and logs per-field
audit.

Arguments are `Job.id` primary keys. Find them with `/applied`.

```!
.claude/bin/dry.sh $ARGUMENTS
```

Report outcome, fields-resolved count per app, the batch LLM model
used, and any `unresolved` warnings.
