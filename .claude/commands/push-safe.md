---
description: "Run full pytest; if green, git push origin HEAD"
allowed-tools: "Bash(python *), Bash(git *)"
---

Safe push: runs the full 720-test suite, and only pushes if every
test passes. Blocks on any failure.

Prevents the "pushed red tests, CI fails, hold-my-beer revert" loop.

```!
echo "── Running full test suite..."
if ! python -m pytest -q 2>&1 | tail -5; then
  echo ""
  echo "✗ Tests failed. Push blocked. Fix tests first."
  exit 1
fi
echo ""
echo "✓ Tests green. Pushing..."
git push origin HEAD
```

Never force-push from here — the `block-destructive.sh` hook catches
`--force` anyway, but this command doesn't pass it through.
