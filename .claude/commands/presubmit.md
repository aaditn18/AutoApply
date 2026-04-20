---
description: "Open the latest pre-submit screenshot for an application"
argument-hint: "<APP_ID>"
allowed-tools: "Bash(.claude/bin/presubmit.sh:*)"
---

Locate and open the pre-submit screenshot
(`state/failed_submits/presubmit_<hash>.png`) for a given application.
Hash is md5(form_url)[:10].

The screenshot shows React-Select state (singleValue text) at click
time — use to verify whether `fillers/react_select.py` committed the
right option.

```!
.claude/bin/presubmit.sh $ARGUMENTS
```

Eyeball which fields are filled (white) vs empty (grey placeholder).
Compare to `/audit` output for the same id.
