#!/usr/bin/env bash
# /dry — dry-run the resolver on one or more Job.id. No Playwright;
# dumps the payload to state/dry_runs/ and logs per-field audit.
set -eo pipefail
cd "$(dirname "$0")/../.."
source "$(dirname "$0")/_lib.sh"

if [ $# -eq 0 ]; then
  echo "Usage: /dry <JOB_ID> [JOB_ID ...]"
  exit 0
fi

"$PY" scripts/apply_by_job_ids.py "$@" --pace 2 2>&1 | tail -60
