#!/usr/bin/env bash
# Boot uvicorn (api/) + next dev (web/) in parallel and tail both.
#
# Usage:
#   make dev
#
# Cleans up both children on Ctrl+C. If web/node_modules is missing it
# runs `npm install` first.

set -euo pipefail

cd "$(dirname "$0")/.."

# Web deps
if [[ ! -d "web/node_modules" ]]; then
  echo "[dev.sh] installing web/ deps (one-time)…"
  (cd web && npm install)
fi

# Trap children
PIDS=()
cleanup() {
  echo
  echo "[dev.sh] shutting down…"
  for pid in "${PIDS[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait
}
trap cleanup INT TERM EXIT

# Start uvicorn
echo "[dev.sh] starting uvicorn on http://127.0.0.1:8765"
uvicorn api.main:app --reload --host 127.0.0.1 --port 8765 \
  2>&1 | sed -u 's/^/[api] /' &
PIDS+=("$!")

# Start next dev
echo "[dev.sh] starting next dev on http://127.0.0.1:3765"
(cd web && npm run dev) 2>&1 | sed -u 's/^/[web] /' &
PIDS+=("$!")

echo
echo "[dev.sh] open http://127.0.0.1:3765  (Ctrl+C to stop)"
wait
