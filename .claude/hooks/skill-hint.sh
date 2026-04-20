#!/usr/bin/env bash
# UserPromptSubmit hook — pattern-match the user prompt against
# .claude/hooks/skill-hints.yml and inject a one-line pointer when a
# known pattern fires. Removes the "did Claude notice there's a
# skill/guide for this?" failure mode.
#
# The YAML file is the data; this script is just the dispatcher.
# Patterns are case-insensitive ERE regex. First match wins.
#
# Output to stdout gets added to Claude's context. Keep output empty
# when nothing matches — don't pollute every prompt.

set -eo pipefail

cd "$(dirname "$0")/../.." 2>/dev/null || exit 0
here="$(cd "$(dirname "$0")" && pwd)"
yml="$here/skill-hints.yml"

[ -f "$yml" ] || exit 0

# Read the user prompt from the stdin JSON.
json="$(cat || true)"
prompt="$(
  printf '%s' "$json" | python3 -c '
import json, sys
try:
    data = json.loads(sys.stdin.read() or "{}")
    print(data.get("prompt") or data.get("user_prompt") or "")
except Exception:
    pass
' 2>/dev/null || true
)"

[ -z "$prompt" ] && exit 0

# Match the prompt against each pattern; emit the first hit and stop.
python3 <<EOF 2>/dev/null || true
import re
import sys

# Inline-safe YAML parse: we only need patterns + hints, and we
# control the file format. Avoid importing yaml to keep this hook
# dependency-free.
text = open(r"$yml", "r", encoding="utf-8").read()
prompt = r"""$prompt"""

# Simple parser for our restricted schema:
#   - pattern: '...'
#     hint: '...'
entries = []
pat = None
for line in text.splitlines():
    s = line.strip()
    if not s or s.startswith("#") or s == "patterns:":
        continue
    if s.startswith("- pattern:"):
        pat = s.split(":", 1)[1].strip().strip("'\"")
    elif s.startswith("hint:") and pat is not None:
        hint = s.split(":", 1)[1].strip().strip("'\"")
        entries.append((pat, hint))
        pat = None

for pattern, hint in entries:
    try:
        if re.search(pattern, prompt, re.IGNORECASE):
            # Emit one line; prefix with [hint] so Claude knows it's a
            # hook-injected hint, not user text.
            print(f"[hint] {hint}")
            sys.exit(0)
    except re.error:
        continue
EOF

exit 0
