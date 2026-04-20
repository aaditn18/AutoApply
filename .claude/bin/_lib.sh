#!/usr/bin/env bash
# Shared bash helpers for .claude/bin/ scripts. Source, don't exec.
#
# PY — picks the right python interpreter. Prefers the project venv
# (where all runtime deps like pyyaml, google-genai, playwright live),
# falls back to python3 / python.

pick_python() {
  if [ -x ".venv/bin/python" ]; then
    echo ".venv/bin/python"
    return
  fi
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
