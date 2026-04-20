---
description: "Dump batch_audit + resolved fields for a specific Application.id"
argument-hint: "<APP_ID>"
allowed-tools: "Bash(sqlite3 *)"
---

Full post-mortem for one application: the batch LLM audit trail
(which model answered, how many Qs, cascade history, any error)
plus the job metadata.

Use after a real submit to see exactly which fields were resolved by
the classifier, which went through the Gemini batch, and which failed.

```!
APP_ID="${1:-0}"
if [ "$APP_ID" = "0" ]; then
  echo "Usage: /audit <APP_ID> — find one via /applied"
  exit 0
fi
sqlite3 state/jobs.sqlite -box "
  SELECT
    a.id, a.outcome, a.error_code, a.error_message,
    a.dry_run, a.submitted_at,
    j.company, j.title, j.url
  FROM applications a
  JOIN jobs j ON a.job_id = j.id
  WHERE a.id = $APP_ID
"
echo ""
echo "── batch_audit ───────────────────────────────────────────────"
sqlite3 state/jobs.sqlite "
  SELECT json_extract(artifacts, '\$.batch_audit')
  FROM applications WHERE id = $APP_ID
" | python3 -c '
import json, sys
raw = sys.stdin.read().strip()
if not raw or raw == "None":
    print("(no batch_audit)")
else:
    try:
        print(json.dumps(json.loads(raw), indent=2))
    except Exception:
        print(raw)
'
```

Report: outcome, which model answered, how many questions were
batched, any `needs_review` answers, cascade fallback trace.
