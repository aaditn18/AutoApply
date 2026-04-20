#!/usr/bin/env bash
# PreToolUse(Bash) hook — catch undocumented structural changes
# before `git commit` / `git push`. Does NOT edit docs automatically
# (auto-generated docs produce noise). Instead, reminds the user (and
# Claude) which doc files likely need a manual update.
#
# Fires only on `git commit` / `git push` commands. Analyzes:
#   - Staged changes (for commit)
#   - origin/main..HEAD + staged changes (for push)
# Maps each changed code path to its doc file(s) via
# .claude/hooks/docs-sync-map.yml. Flags doc files that were NOT
# themselves touched in the current diff.
#
# Non-blocking: exit 0 always. Warning goes to stderr so Claude sees
# it and can offer to update docs, and the user sees it in the tool
# output. The commit/push still proceeds.
#
# To disable for a session, use .claude/settings.local.json:
#   {"disableAllHooks": true}

set -eo pipefail

cd "$(dirname "$0")/../.." 2>/dev/null || exit 0

json="$(cat || true)"

command_str="$(
  printf '%s' "$json" | python3 -c '
import json, sys
try:
    data = json.loads(sys.stdin.read() or "{}")
    ti = data.get("tool_input") or {}
    print((ti.get("command") or "").strip())
except Exception:
    pass
' 2>/dev/null || true
)"

# Only inspect git commit / git push invocations.
case "$command_str" in
  *"git commit"*|*"git push"*) ;;
  *) exit 0 ;;
esac

# Skip if amending or dry-running — no new state to document.
case "$command_str" in
  *"--dry-run"*|*"--amend"*) exit 0 ;;
esac

# Skip if the only change is state/jobs.sqlite (auto-pipeline commits
# are one-file DB snapshots, not structural code changes).
diff_files="$(git diff --cached --name-only 2>/dev/null || true)"
if [ -z "$diff_files" ]; then
  diff_files="$(git diff origin/main..HEAD --name-only 2>/dev/null || true)"
fi
[ -z "$diff_files" ] && exit 0

# Drop jobs.sqlite from consideration; it's noise.
changed_files="$(printf '%s\n' "$diff_files" | grep -v '^state/jobs\.sqlite$' || true)"
[ -z "$changed_files" ] && exit 0

# Run the analysis via Python — YAML parsing + glob matching + set
# diff is cleaner there than in bash.
python3 <<PY || true
import fnmatch
import os
import sys
from pathlib import Path

REPO = Path(os.getcwd())
MAP_PATH = REPO / ".claude/hooks/docs-sync-map.yml"
if not MAP_PATH.is_file():
    sys.exit(0)

# ---- Minimal YAML parser (inline; dependency-free) ---------------
# Schema we expect:
#   mappings:
#     - paths: [glob, ...]
#       docs:  [doc_path, ...]
#   test_count_files: [path, ...]
def parse_map(text):
    mappings = []
    test_files = []
    mode = None  # "mapping_paths" | "mapping_docs" | "test_count"
    current = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())

        if stripped == "mappings:":
            mode = "mapping_root"
            continue
        if stripped == "test_count_files:":
            mode = "test_count"
            continue
        if mode == "mapping_root" and stripped.startswith("- paths:"):
            current = {"paths": [], "docs": []}
            mappings.append(current)
            mode = "mapping_paths"
            continue
        if mode == "mapping_paths" and stripped == "docs:":
            mode = "mapping_docs"
            continue
        if mode == "mapping_docs" and stripped.startswith("- paths:"):
            current = {"paths": [], "docs": []}
            mappings.append(current)
            mode = "mapping_paths"
            continue
        if stripped.startswith("- "):
            val = stripped[2:].strip().strip('"\'')
            if mode == "mapping_paths":
                current["paths"].append(val)
            elif mode == "mapping_docs":
                current["docs"].append(val)
            elif mode == "test_count":
                test_files.append(val)
    return mappings, test_files

text = MAP_PATH.read_text(encoding="utf-8")
mappings, test_count_files = parse_map(text)

# ---- Changed files (staged + committed-but-unpushed) -------------
changed = set()
for raw in """$changed_files""".splitlines():
    p = raw.strip()
    if p:
        changed.add(p)
if not changed:
    sys.exit(0)

# ---- Match each changed file against each mapping ----------------
suggested_docs = {}      # doc_path -> set of code_paths triggering it
for m in mappings:
    for code_path in changed:
        if any(fnmatch.fnmatch(code_path, pat) for pat in m["paths"]):
            for doc in m["docs"]:
                suggested_docs.setdefault(doc, set()).add(code_path)

# Test-count autosync: if any tests/ file changed, flag the
# test-count doc files specifically.
if any(p.startswith("tests/") for p in changed):
    for doc in test_count_files:
        suggested_docs.setdefault(doc, set()).add("(test count may be stale)")

if not suggested_docs:
    sys.exit(0)

# ---- Docs that WEREN'T touched in this diff ----------------------
untouched = {
    doc: triggers
    for doc, triggers in suggested_docs.items()
    if doc not in changed
}
if not untouched:
    sys.exit(0)

# ---- Emit a concise warning to stderr ----------------------------
# Claude sees stderr in the tool output. Human-readable, non-blocking.
lines = []
lines.append("")
lines.append("┌─ [docs-sync] Undocumented structural changes detected ────────")
lines.append("│ You are about to commit/push code changes that likely need")
lines.append("│ matching updates in these doc files (NOT touched in diff):")
lines.append("│")
for doc in sorted(untouched):
    triggers = sorted(untouched[doc])
    lines.append(f"│   {doc}")
    for t in triggers[:3]:
        lines.append(f"│     ← {t}")
    if len(triggers) > 3:
        lines.append(f"│     ← (+{len(triggers) - 3} more)")
lines.append("│")
lines.append("│ If these doc updates are genuinely not needed, proceed.")
lines.append("│ To disable this hook for this session:")
lines.append("│   echo '{\"disableAllHooks\": true}' > .claude/settings.local.json")
lines.append("└───────────────────────────────────────────────────────────────")
lines.append("")
sys.stderr.write("\n".join(lines) + "\n")
sys.exit(0)
PY

# Always exit 0 — warning is informational.
exit 0
