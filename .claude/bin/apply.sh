#!/usr/bin/env bash
# /apply — REAL submit to one or more Job.id (opens Playwright,
# uploads, fills, clicks submit). Duplicate-risk applies.
set -eo pipefail
cd "$(dirname "$0")/../.."
source "$(dirname "$0")/_lib.sh"

if [ $# -eq 0 ]; then
  echo "Usage: /apply <JOB_ID> [JOB_ID ...]"
  exit 0
fi

echo "⚠ REAL SUBMISSION on job ids: $*"
"$PY" scripts/apply_by_job_ids.py "$@" --no-dry-run --pace 10 2>&1 | tail -40
