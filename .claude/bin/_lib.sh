#!/usr/bin/env bash
# Shared bash helpers for .claude/bin/ scripts. Source, don't exec.
#
# PY — picks the right python interpreter. Prefers the project venv
# (where all runtime deps like pyyaml, google-genai, playwright live).
#
# Worktree-aware: Claude Code spawns subagents in git worktrees
# under .claude/worktrees/<name>/, which don't have their own .venv.
# We walk the git worktree→common-dir chain to find the primary
# checkout's .venv.

pick_python() {
  # 1. Current cwd's .venv (normal case: user runs from repo root).
  if [ -x ".venv/bin/python" ]; then
    echo ".venv/bin/python"
    return
  fi

  # 2. Main repo checkout's .venv via git. `--git-common-dir` resolves
  #    to the shared .git/ for worktrees (or just .git/ normally).
  #    Its parent directory is the primary checkout root.
  if command -v git >/dev/null 2>&1; then
    local common_dir
    common_dir="$(git rev-parse --git-common-dir 2>/dev/null || true)"
    if [ -n "$common_dir" ]; then
      # common_dir can be relative or absolute. Resolve + cd into parent.
      local main_root
      main_root="$(cd "$common_dir/.." 2>/dev/null && pwd || true)"
      if [ -n "$main_root" ] && [ -x "$main_root/.venv/bin/python" ]; then
        echo "$main_root/.venv/bin/python"
        return
      fi
    fi
  fi

  # 3. Walk up from cwd looking for a .venv sibling. Bounded to 6 hops.
  local d="$PWD"
  for _ in 1 2 3 4 5 6; do
    if [ -x "$d/.venv/bin/python" ]; then
      echo "$d/.venv/bin/python"
      return
    fi
    [ "$d" = "/" ] && break
    d="$(dirname "$d")"
  done

  # 4. Fall back to system interpreters.
  if command -v python3 >/dev/null 2>&1; then
    echo "python3"
    return
  fi
  if command -v python >/dev/null 2>&1; then
    echo "python"
    return
  fi
  echo "python3"  # last resort; caller will see the NotFound error
}

PY="$(pick_python)"
export PY
