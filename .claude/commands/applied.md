---
description: "Show recent applications from state/jobs.sqlite (default 10)"
argument-hint: "[N]"
allowed-tools: "Bash(sqlite3 *)"
---

Dump the last `N` (default 10) applications from `state/jobs.sqlite`.
Includes id, company, title, outcome, dry-run flag, submit time, and
which Gemini model (if any) answered the batch.

```!
N="${1:-10}"
sqlite3 state/jobs.sqlite -box "
  SELECT
    a.id,
    j.company,
    substr(j.title, 1, 40) AS title,
    a.outcome,
    CASE a.dry_run WHEN 1 THEN 'dry' ELSE '' END AS dry,
    strftime('%m-%d %H:%M', a.submitted_at) AS ts,
    COALESCE(json_extract(a.artifacts, '\$.batch_audit.model_used'), '') AS model
  FROM applications a
  JOIN jobs j ON a.job_id = j.id
  ORDER BY a.id DESC
  LIMIT $N
"
```

Summarize: recent success rate, any failed/review outcomes, and the
most recent ok submit's timestamp.
