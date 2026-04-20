#!/usr/bin/env bash
# PreToolUse(Bash) hook — block genuinely destructive commands.
#
# state/jobs.sqlite is 18MB of live application history. resumes/ is a
# git submodule pointing at the private resume repo. git push --force
# to main/master risks overwriting the auto-pipeline's commits.
# alembic downgrade + DROP TABLE + DELETE FROM applications all
# nuke data we can't reconstruct.
#
# This hook inspects the command string from the tool-use JSON, emits
# a blocking message on match (exit 2), and passes through otherwise
# (exit 0).
#
# Lower-risk patterns (rm -rf on anything besides state/, resumes/)
# are not touched — that would over-police and create friction. The
# matcher catches only the genuinely catastrophic cases.

set -eo pipefail

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

# Empty = nothing to inspect.
[ -z "$command_str" ] && exit 0

block() {
  local reason="$1"
  printf '{"decision":"block","reason":%s}\n' "$(
    python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$reason"
  )"
  exit 2
}

# jobs.sqlite — 18MB of live application state.
if printf '%s' "$command_str" | grep -qE '(rm\s+-[rRf]+\s+[^|;&]*state/jobs\.sqlite|>\s*state/jobs\.sqlite)'; then
  block "Refusing to destroy state/jobs.sqlite (18MB of live application history). If this is deliberate, use .claude/settings.local.json disableAllHooks:true for this session. Recovery path from git reflog demonstrated in today's rebase."
fi

# resumes/ submodule.
if printf '%s' "$command_str" | grep -qE 'rm\s+-[rRf]+\s+[^|;&]*resumes(/|\s|$)'; then
  block "Refusing to destroy resumes/ submodule. Use 'git submodule deinit' if you really want to remove it."
fi

# git push --force to main.
if printf '%s' "$command_str" | grep -qE 'git\s+push.*--force\b' \
   && printf '%s' "$command_str" | grep -qE '(main|master|HEAD)'; then
  block "Refusing to force-push to main/master. If you really need to, run the command with FORCE_CONFIRM=1 prefix or use .claude/settings.local.json disableAllHooks."
fi

# git reset --hard to origin/*.
if printf '%s' "$command_str" | grep -qE 'git\s+reset\s+--hard\s+origin/'; then
  block "Refusing 'git reset --hard origin/*' (discards local commits). Use 'git stash' + reset, or pass through .claude/settings.local.json disableAllHooks."
fi

# alembic downgrade.
if printf '%s' "$command_str" | grep -qE 'alembic\s+downgrade'; then
  block "Refusing alembic downgrade (destroys application rows). Take a sqlite3 .dump backup first, then disable this hook for the session."
fi

# DROP TABLE / DELETE FROM applications via sqlite3.
if printf '%s' "$command_str" | grep -qEi 'sqlite3.*(DROP\s+TABLE|DELETE\s+FROM\s+applications)'; then
  block "Refusing bulk DROP/DELETE on applications (live application history). Use a filtered UPDATE or take a backup first."
fi

# Pass through.
exit 0
