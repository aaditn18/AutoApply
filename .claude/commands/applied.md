---
description: "Show recent applications from state/jobs.sqlite (default 10)"
argument-hint: "[N]"
allowed-tools: "Bash(.claude/bin/applied.sh:*)"
---

Dump the last `N` (default 10) applications — id, company, title,
outcome, dry-run flag, submit time, and batch model used.

```!
.claude/bin/applied.sh $ARGUMENTS
```

Summarize recent success rate and any failed/review outcomes.
