#!/usr/bin/env bash
# UserPromptSubmit hook — inject a compact one-liner of repo state
# before Claude sees the prompt. Keeps Claude situationally aware
# without the user re-typing "what's the current state?" every turn.
#
# Output goes to stdout and is included in the prompt context. Keep
# it tight — every extra token costs.

set -eo pipefail

cd "$(dirname "$0")/../.." 2>/dev/null || exit 0

# Read and discard stdin (the prompt JSON — we don't need it).
cat >/dev/null || true

# Skip entirely if this isn't a git repo.
git rev-parse --git-dir >/dev/null 2>&1 || exit 0

branch="$(git symbolic-ref --short -q HEAD 2>/dev/null || echo '(detached)')"

# Compact working-tree summary: M/A/D file counts.
modified=$(git status --porcelain=v1 2>/dev/null | grep -cE '^\s*M') || modified=0
added=$(git status --porcelain=v1 2>/dev/null | grep -cE '^\s*A|^\?') || added=0
ahead=$(git rev-list --count @{u}..HEAD 2>/dev/null || echo 0)
behind=$(git rev-list --count HEAD..@{u} 2>/dev/null || echo 0)

# Single-line summary. Only print if there's something interesting.
if [ "$modified" -eq 0 ] && [ "$added" -eq 0 ] && [ "$ahead" -eq 0 ] && [ "$behind" -eq 0 ]; then
  # Clean tree — emit a minimal marker so Claude knows the state was checked.
  echo "[repo] branch=$branch clean"
else
  echo "[repo] branch=$branch modified=$modified untracked=$added ahead=$ahead behind=$behind"
fi

exit 0
