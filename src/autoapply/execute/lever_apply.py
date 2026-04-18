"""Lever applicator — HTTP fast path.

Lever postings include their form spec inline in the public API response
(no separate "questions" endpoint). A typical posting looks like:

    {
      "id": "abc123",
      "text": "Software Engineer",
      "hostedUrl": "https://jobs.lever.co/netflix/abc123",
      "applyUrl":  "https://jobs.lever.co/netflix/abc123/apply",
      "additional": "...",
      "lists":     [...],
      "additionalPlain": "...",
      "categories": {...},
      "workplaceType": "hybrid",

      // these two are the form spec:
      "customQuestions": [
          {
             "id": "q-12",
             "text": "Will you now or in the future require visa sponsorship?",
             "type": "text",
             "required": true,
             "options": []
          },
          ...
      ],

      "additionalQuestions": [...]   // some boards use this field name
    }

Lever's Easy Apply POST endpoint is
    POST https://api.lever.co/v0/postings/<token>/<postingId>/apply
but like Greenhouse, public submission requires CSRF + resume upload;
real submit is Phase 2 via Playwright. MVP: fetch + resolve + dry-run.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

import httpx

from autoapply.config import get_settings
from autoapply.execute.base import Applicator, ApplyResult
from autoapply.execute.standard_fields import FieldSpec, ResolvedField
from autoapply.tracker.models import Job


log = logging.getLogger(__name__)


LEVER_BASE = "https://api.lever.co/v0/postings"


# -- Standard fields Lever always wants -----------------------------------


_BASE_FIELDS: list[FieldSpec] = [
    FieldSpec(name="name", label="Full Name", required=True, kind="text"),
    FieldSpec(name="email", label="Email", required=True, kind="text"),
    FieldSpec(name="phone", label="Phone", required=False, kind="text"),
    FieldSpec(name="resume", label="Resume / CV", required=True, kind="file"),
    # NOTE: Lever's DOM uses capitalised keys (urls[LinkedIn], urls[GitHub], etc.)
    # even though the API docs show lowercase.  Use the DOM name so _fill_field
    # can locate the element by its `name` attribute.
    FieldSpec(
        name="urls[LinkedIn]", label="LinkedIn URL", required=False, kind="text"
    ),
    FieldSpec(
        name="urls[GitHub]", label="GitHub URL", required=False, kind="text"
    ),
    FieldSpec(
        name="urls[Portfolio]", label="Portfolio / Website", required=False, kind="text"
    ),
    FieldSpec(
        name="cover_letter", label="Cover Letter", required=False, kind="file"
    ),
    # EEO fields — present on every Lever form.  Options below are approximate;
    # _snap_to_option does a case-insensitive substring match at fill time.
    FieldSpec(
        name="eeo[gender]",
        label="Gender",
        required=False,
        kind="select",
        options=["Decline to self-identify"],
    ),
    FieldSpec(
        name="eeo[race]",
        label="Race / Ethnicity",
        required=False,
        kind="select",
        options=["Decline to self-identify"],
    ),
    FieldSpec(
        name="eeo[veteran]",
        label="Veteran Status",
        required=False,
        kind="select",
        options=["I am not a protected veteran"],
    ),
    FieldSpec(
        name="eeo[disability]",
        label="Disability Status",
        required=False,
        kind="select",
        options=["I don't wish to answer"],
    ),
]


def _kind_from_type(t: str) -> str:
    t = (t or "").lower()
    if t in ("text",):
        return "text"
    if t in ("textarea", "text-area", "long-text"):
        return "textarea"
    if t in ("dropdown", "single-select", "select"):
        return "select"
    if t in ("multiple-select", "multi-select", "multi_select"):
        return "multi_select"
    if t in ("yes/no",):
        return "select"
    if t in ("file",):
        return "file"
    return "text"


def _question_to_specs(q: dict[str, Any]) -> Iterable[FieldSpec]:
    qid = str(q.get("id") or q.get("fieldId") or "").strip()
    label = str(q.get("text") or q.get("label") or "").strip()
    if not qid or not label:
        return
    required = bool(q.get("required") or False)
    kind = _kind_from_type(str(q.get("type") or ""))
    options_raw = q.get("options") or q.get("values") or []
    options: list[str] = []
    if isinstance(options_raw, list):
        for o in options_raw:
            if isinstance(o, dict):
                lbl = o.get("text") or o.get("label") or o.get("optionText") or o.get("value")
                if lbl:
                    options.append(str(lbl))
            elif isinstance(o, str):
                options.append(o)
    yield FieldSpec(
        name=f"cards[{qid}]",
        label=label,
        required=required,
        kind=kind,
        options=options,
    )


# -- Applicator -------------------------------------------------------------


class LeverApplicator(Applicator):
    name = "lever"

    def __init__(
        self,
        *args,
        client: httpx.Client | None = None,
        timeout: float = 20.0,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._client = client or httpx.Client(
            timeout=timeout,
            headers={"User-Agent": "AutoApply/0.1", "Accept": "application/json"},
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    # ---- Form fetch ----------------------------------------------------

    def fetch_form(self, job: Job) -> list[FieldSpec]:
        url = f"{LEVER_BASE}/{job.board_token}/{job.source_id}?mode=json"
        resp = self._client.get(url)
        resp.raise_for_status()
        data = resp.json()
        return list(self._parse_posting(data))

    def _parse_posting(self, data: dict[str, Any]) -> Iterable[FieldSpec]:
        yield from _BASE_FIELDS
        for key in ("customQuestions", "additionalQuestions"):
            block = data.get(key)
            if not isinstance(block, list):
                continue
            for q in block:
                if isinstance(q, dict):
                    yield from _question_to_specs(q)

    # ---- Payload build -------------------------------------------------

    def build_payload(self, job: Job, resolved: list[ResolvedField]) -> dict[str, Any]:
        data: dict[str, Any] = {}
        files: dict[str, str] = {}
        for r in resolved:
            if r.source == "machine_key" and r.name in ("resume", "cover_letter"):
                if r.value:
                    files[r.name] = r.value
                continue
            if r.value:
                data[r.name] = r.value
        return {"data": data, "files": files, "posting_id": job.source_id}

    # ---- Submit --------------------------------------------------------

    def submit(self, job: Job, payload: dict[str, Any]) -> ApplyResult:
        """Submit via Playwright (stealth browser).

        Called only when dry_run=False. Navigates
        jobs.lever.co/<token>/<id>/apply, fills every resolved field,
        uploads the resume PDF, and clicks submit. Returns:
            outcome="ok"      — success confirmed on the post-submit page.
            outcome="captcha" — CAPTCHA wall encountered; job goes to review.
            outcome="failed"  — any other error.
        """
        from autoapply.execute.playwright_submit import (
            CaptchaDetected,
            SubmitFailed,
            submit_lever,
        )

        settings = get_settings()
        try:
            result = submit_lever(
                token=job.board_token,
                posting_id=str(job.source_id),
                data=payload.get("data", {}),
                files=payload.get("files", {}),
                headless=True,
                hcaptcha_accessibility_token=settings.HCAPTCHA_ACCESSIBILITY_TOKEN,
                imap_server=settings.IMAP_SERVER,
                imap_port=settings.IMAP_PORT,
                imap_email=settings.IMAP_EMAIL,
                imap_password=settings.IMAP_PASSWORD,
                imap_code_timeout=settings.IMAP_CODE_TIMEOUT,
            )
        except CaptchaDetected as exc:
            log.warning("CAPTCHA detected for job %s: %s", job.canonical_key, exc)
            return ApplyResult(
                outcome="captcha",
                error_code="captcha",
                error_message=str(exc),
            )
        except SubmitFailed as exc:
            log.error("submit failed for job %s: %s", job.canonical_key, exc)
            return ApplyResult(
                outcome="failed",
                error_code="submit_failed",
                error_message=str(exc),
            )
        except Exception as exc:
            log.exception("unexpected error submitting job %s", job.canonical_key)
            return ApplyResult(
                outcome="failed",
                error_code="playwright_error",
                error_message=str(exc),
            )

        if result["ok"]:
            log.info(
                "submitted OK: job=%s url=%s field_errors=%s",
                job.canonical_key,
                result["url"],
                result["field_errors"],
            )
            return ApplyResult(
                outcome="ok",
                answers=payload.get("data", {}),
                artifacts={
                    "final_url": result["url"],
                    "field_errors": result["field_errors"],
                },
            )

        return ApplyResult(
            outcome="failed",
            error_code="no_confirmation",
            error_message=result.get("error") or "no success signal on post-submit page",
            artifacts={
                "final_url": result["url"],
                "field_errors": result["field_errors"],
            },
        )
