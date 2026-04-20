---
description: "Dry-run apply to one or more Job.id — wraps scripts/apply_by_job_ids.py"
argument-hint: "<JOB_ID> [JOB_ID ...]"
allowed-tools: "Bash(python *)"
---

Dry-run one or more specific jobs through the full resolution
pipeline (classifier + batch LLM + audit). Does NOT open Playwright
— the resolver dumps the payload to `state/dry_runs/` and logs the
per-field audit.

Arguments are `Job.id` primary keys (integers). Find them with
`/applied` or a direct sqlite query.

Example: `/dry 1285 1422` → resolves both jobs, dumps audits.

```!
python scripts/apply_by_job_ids.py $ARGUMENTS --pace 2 2>&1 | tail -60
```

Report the outcome, the number of fields resolved per app, the model
the batch LLM used, and any `unresolved` warnings.
