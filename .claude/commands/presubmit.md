---
description: "Open the latest pre-submit screenshot for an application"
argument-hint: "<APP_ID>"
allowed-tools: "Bash(sqlite3 *), Bash(open *), Bash(ls *)"
---

Locate and open the pre-submit screenshot (`state/failed_submits/presubmit_<hash>.png`)
for a given application. The hash is an md5 of the form URL.

Use when diagnosing a real-submit failure or review routing — the
screenshot shows React-Select state (singleValue text) at click time,
which is how you verify whether `fillers/react_select.py` committed
the right option.

```!
APP_ID="${1:-0}"
if [ "$APP_ID" = "0" ]; then
  echo "Usage: /presubmit <APP_ID>"
  exit 0
fi

URL="$(sqlite3 state/jobs.sqlite "SELECT j.url FROM applications a JOIN jobs j ON a.job_id = j.id WHERE a.id = $APP_ID")"
if [ -z "$URL" ]; then
  echo "No application id=$APP_ID"
  exit 0
fi

HASH="$(python3 -c "import hashlib; print(hashlib.md5('$URL'.encode()).hexdigest()[:10])")"
SHOT="state/failed_submits/presubmit_${HASH}.png"

if [ -f "$SHOT" ]; then
  echo "Opening $SHOT"
  open "$SHOT"
else
  echo "No screenshot at $SHOT — most recent ones:"
  ls -lt state/failed_submits/presubmit_*.png 2>/dev/null | head -5
fi
```

After opening: eyeball which fields are filled (white) vs empty
(grey placeholder). Compare to the `/audit` output for the same
app id.
