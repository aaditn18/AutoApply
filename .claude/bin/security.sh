#!/usr/bin/env bash
# /security — SecurityEvent rows from the last N days (default 7).
set -eo pipefail
cd "$(dirname "$0")/../.."

DAYS="${1:-7}"
case "$DAYS" in
  ''|*[!0-9]*) echo "Invalid days: $DAYS"; exit 1 ;;
esac

sqlite3 state/jobs.sqlite -box "
  SELECT
    id, kind, substr(snippet, 1, 60) AS snippet,
    pattern_matched,
    strftime('%m-%d %H:%M', created_at) AS ts,
    job_id
  FROM security_events
  WHERE created_at > datetime('now', '-$DAYS days')
  ORDER BY id DESC
  LIMIT 50
"
