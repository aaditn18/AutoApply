---
description: "Dump SecurityEvent rows from the last N days (default 7)"
argument-hint: "[days]"
allowed-tools: "Bash(.claude/bin/security.sh:*)"
---

Show prompt-injection attempts + other security events from the
last `N` days. Weekly review of this output is the backstop for
the prompt-injection defense.

```!
.claude/bin/security.sh $ARGUMENTS
```

Report total count, unique `kind` distribution, and any pattern
firing repeatedly (potential honeypot source).
