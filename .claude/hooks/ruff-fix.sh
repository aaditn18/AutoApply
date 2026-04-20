#!/usr/bin/env bash
# PostToolUse hook — auto-format + auto-lint any Python file that was
# just edited by Claude. Matches CI's ruff config exactly (see
# pyproject.toml lines 51-57). Fails soft: a broken hook should never
# block the user's workflow.
#
# Invocation (from Claude Code): stdin is JSON including
#   {"tool_input":{"file_path":"/abs/path/to/file.py"}, ...}
# Exit codes: 0 always (we don't want to block edits on lint).

set -eo pipefail

# Read the tool-use JSON from stdin; extract the edited file path.
json="$(cat || true)"

# Parse file_path from the JSON. Use python rather than jq so this
# works on fresh machines without installing jq.
file_path="$(
  printf '%s' "$json" | python3 -c '
import json, sys
try:
    data = json.loads(sys.stdin.read() or "{}")
    ti = data.get("tool_input") or {}
    p = ti.get("file_path") or ""
    print(p)
except Exception:
    pass
' 2>/dev/null || true
)"

# Only act on .py files under src/ or tests/. The matcher in
# settings.json is the primary gate but we belt-and-suspender here.
case "$file_path" in
  *.py) ;;
  *) exit 0 ;;
esac
case "$file_path" in
  *src/*|*tests/*) ;;
  *) exit 0 ;;
esac

if ! command -v ruff >/dev/null 2>&1; then
  # Ruff not installed — silently skip. User will see lint in CI.
  exit 0
fi

# Fix-what-can-be-fixed, then format. Both are single-file fast.
# Output suppressed unless something errors; if ruff surfaces real
# problems, we emit a brief note to stderr and still exit 0 so the
# edit isn't blocked.
{
  ruff check --fix --quiet "$file_path" 2>&1 || true
  ruff format --quiet "$file_path" 2>&1 || true
} >/dev/null

exit 0
