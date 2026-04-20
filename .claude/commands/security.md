---
description: "Dump SecurityEvent rows from the last N days (default 7)"
argument-hint: "[days]"
allowed-tools: "Bash(sqlite3 *)"
---

Show prompt-injection attempts + other security events recorded by
`autoapply.security.injection_guard` in the last `N` days.

Every JD scraped during ingest goes through the injection scanner;
any hit writes a `SecurityEvent` row with the matched pattern and
snippet. Weekly review of this output is the backstop documented in
the architectural plan.

```!
DAYS="${1:-7}"
sqlite3 state/jobs.sqlite -box "
  SELECT
    id, kind, substr(snippet, 1, 60) AS snippet,
    pattern_matched,
    strftime('%m-%d %H:%M', created_at) AS ts,
    job_id
  FROM security_events
  WHERE created_at > datetime('now', '-$DAYS days')
  ORDER BY id DESC
  LIMIT 50
"
```

Report: total count, unique `kind` distribution, and any patterns
that fired repeatedly (potential honeypot source).
