---
description: "REAL SUBMIT to one or more Job.id (Playwright + form fill + click) — use with care"
argument-hint: "<JOB_ID> [JOB_ID ...]"
allowed-tools: "Bash(.claude/bin/apply.sh:*)"
---

**⚠ REAL SUBMISSION — NOT DRY RUN.** Opens a Playwright browser,
uploads resume, fills form, clicks submit.

Before running, confirm with the user that:
1. These job IDs are intentional (check `/applied` first).
2. No recent successful submission for the same company unless
   explicitly wanted.
3. `GEMINI_API_KEY`, `IMAP_EMAIL`, `IMAP_PASSWORD` are set — the OTP
   verification path may fire.

Takes ~1-2 min per app. Pace 10s between apps.

```!
.claude/bin/apply.sh $ARGUMENTS
```

Report per-job outcome (`ok` / `failed` / `review` / `captcha`). On
failure, use `/presubmit <id>` to inspect the screenshot.
