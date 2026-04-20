#!/usr/bin/env bash
# /test — full pytest or -k-filtered subset.
#
# Zero args → full suite (~1.2s). With args → passed as -k expression.
set -eo pipefail
cd "$(dirname "$0")/../.."
source "$(dirname "$0")/_lib.sh"

if [ $# -eq 0 ]; then
  "$PY" -m pytest -q 2>&1 | tail -8
else
  "$PY" -m pytest -q -k "$*" 2>&1 | tail -12
fi
