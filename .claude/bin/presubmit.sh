#!/usr/bin/env bash
# /presubmit — open the pre-submit screenshot for an application.
# Hash is md5(url)[:10]; see driver._save_presubmit_screenshot.
set -eo pipefail
cd "$(dirname "$0")/../.."
source "$(dirname "$0")/_lib.sh"

APP_ID="${1:-0}"
case "$APP_ID" in
  ''|*[!0-9]*) echo "Usage: /presubmit <APP_ID>"; exit 0 ;;
esac
if [ "$APP_ID" = "0" ]; then
  echo "Usage: /presubmit <APP_ID>"
  exit 0
fi

URL="$(
  sqlite3 state/jobs.sqlite \
    "SELECT j.url FROM applications a JOIN jobs j ON a.job_id = j.id WHERE a.id = $APP_ID"
)"
if [ -z "$URL" ]; then
  echo "No application id=$APP_ID"
  exit 0
fi

# Pass URL via env var so punctuation in it doesn't break the python -c.
export PRESUBMIT_URL="$URL"
HASH="$("$PY" -c "import os, hashlib; print(hashlib.md5(os.environ['PRESUBMIT_URL'].encode()).hexdigest()[:10])")"
SHOT="state/failed_submits/presubmit_${HASH}.png"

if [ -f "$SHOT" ]; then
  echo "Opening $SHOT"
  open "$SHOT"
else
  echo "No screenshot at $SHOT — most recent ones:"
  ls -lt state/failed_submits/presubmit_*.png 2>/dev/null | head -5
fi
