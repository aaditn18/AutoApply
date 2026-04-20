---
description: "Run full pytest; if green, git push origin HEAD"
allowed-tools: "Bash(.claude/bin/push-safe.sh:*)"
---

Safe push: runs the full 720-test suite, and only pushes if every
test passes. Blocks on any failure.

Prevents the "pushed red tests, CI fails, hold-my-beer revert" loop.

```!
.claude/bin/push-safe.sh
```

Never force-pushes. `block-destructive.sh` would catch `--force`
anyway, but this command doesn't pass it through.
