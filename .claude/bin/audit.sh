#!/usr/bin/env bash
# /audit — full post-mortem for one application id:
# job metadata + batch_audit JSON + cascade trace.
set -eo pipefail
cd "$(dirname "$0")/../.."
source "$(dirname "$0")/_lib.sh"

APP_ID="${1:-0}"
case "$APP_ID" in
  ''|*[!0-9]*) echo "Usage: /audit <APP_ID>"; exit 0 ;;
esac
if [ "$APP_ID" = "0" ]; then
  echo "Usage: /audit <APP_ID> — find one via /applied"
  exit 0
fi

sqlite3 state/jobs.sqlite -box "
  SELECT
    a.id, a.outcome, a.error_code, a.error_message,
    a.dry_run, a.submitted_at,
    j.company, j.title, j.url
  FROM applications a
  JOIN jobs j ON a.job_id = j.id
  WHERE a.id = $APP_ID
"

echo ""
echo "── batch_audit ───────────────────────────────────────────────"
sqlite3 state/jobs.sqlite "
  SELECT json_extract(artifacts, '\$.batch_audit')
  FROM applications WHERE id = $APP_ID
" | "$PY" -c '
import json, sys
raw = sys.stdin.read().strip()
if not raw or raw == "None":
    print("(no batch_audit)")
else:
    try:
        print(json.dumps(json.loads(raw), indent=2))
    except Exception:
        print(raw)
'
