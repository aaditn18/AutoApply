#!/usr/bin/env bash
# PostToolUse hook — whenever a YAML rule or prompt template is
# edited, run the rules-loader smoke suite (~0.2s) so schema breaks
# surface at edit time, not at apply time.
#
# Runs ONLY tests/test_rules_loader.py. Never blocks: a failing test
# gets surfaced to stderr but exit 0 so Claude can see the output and
# decide how to respond.
#
# The full test suite is too slow for a save-time hook. Use the /test
# slash command for full-suite runs.

set -eo pipefail

cd "$(dirname "$0")/../.."

if ! command -v python3 >/dev/null 2>&1; then
  exit 0
fi

# Run the schema smoke tests. Capture output so we can emit a concise
# line on failure without dumping all 16 pass lines.
out="$(python3 -m pytest tests/test_rules_loader.py -q --tb=line 2>&1 || true)"

# Look for any failures in the summary line.
if printf '%s' "$out" | grep -qE '[0-9]+ failed'; then
  echo "[.claude/hooks/rules-smoke] RULES SMOKE FAILED — see below:" >&2
  printf '%s\n' "$out" >&2
  # Exit 0: we surface the failure but don't block the edit. Claude
  # (or the user) can decide to revert or fix.
  exit 0
fi

# Silent on success to avoid log spam.
exit 0
