#!/usr/bin/env bash
# /applied — last N applications (default 10) from state/jobs.sqlite.
set -eo pipefail
cd "$(dirname "$0")/../.."

N="${1:-10}"
# Validate N is a positive integer to avoid SQL injection via the
# LIMIT clause.
case "$N" in
  ''|*[!0-9]*) echo "Invalid count: $N"; exit 1 ;;
esac

sqlite3 state/jobs.sqlite -box "
  SELECT
    a.id,
    j.company,
    substr(j.title, 1, 40) AS title,
    a.outcome,
    CASE a.dry_run WHEN 1 THEN 'dry' ELSE '' END AS dry,
    strftime('%m-%d %H:%M', a.submitted_at) AS ts,
    COALESCE(json_extract(a.artifacts, '\$.batch_audit.model_used'), '') AS model
  FROM applications a
  JOIN jobs j ON a.job_id = j.id
  ORDER BY a.id DESC
  LIMIT $N
"
