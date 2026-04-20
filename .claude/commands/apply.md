---
description: "REAL SUBMIT to one or more Job.id (Playwright + form fill + click) — use with care"
argument-hint: "<JOB_ID> [JOB_ID ...]"
allowed-tools: "Bash(python *)"
---

**⚠ REAL SUBMISSION — NOT DRY RUN.** This opens a Playwright browser,
uploads the resume, fills the form, and clicks submit on every
`Job.id` passed. Duplicates are the submitter's responsibility to
avoid; if these jobs were already submitted, recruiters get duplicate
emails.

Before running, confirm with the user that:
1. These job IDs are intentional (check with `/applied` first).
2. No recent successful submission exists for the same company unless
   the user explicitly wants a re-submit.
3. `GEMINI_API_KEY`, `IMAP_EMAIL`, `IMAP_PASSWORD` are set — real
   submits may need the OTP verification path.

Takes ~1-2 min per app (browser launch + stealth + upload + fill +
submit + verify). Pace 10s between apps. Total runtime for N apps:
`(90 + 10) * N` seconds roughly.

```!
echo "⚠ REAL SUBMISSION on job ids: $ARGUMENTS"
python scripts/apply_by_job_ids.py $ARGUMENTS --no-dry-run --pace 10 2>&1 | tail -40
```

Report per-job outcome (`ok` / `failed` / `review` / `captcha`) and
the final RESULTS block. If any `failed`, inspect the latest
`state/failed_submits/presubmit_*.png` screenshot (use `/presubmit`).
