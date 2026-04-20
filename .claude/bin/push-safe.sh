#!/usr/bin/env bash
# /push-safe — run full pytest; if green, git push origin HEAD.
# Blocks on any test failure. Never passes --force.
set -eo pipefail
cd "$(dirname "$0")/../.."
source "$(dirname "$0")/_lib.sh"

echo "── Running full test suite..."
if ! "$PY" -m pytest -q 2>&1 | tail -5; then
  echo ""
  echo "✗ Tests failed. Push blocked. Fix tests first."
  exit 1
fi
echo ""
echo "✓ Tests green. Pushing..."
git push origin HEAD
