"""Greenhouse applicator — HTTP fast path.

Greenhouse exposes form metadata for every posting at
    GET https://boards-api.greenhouse.io/v1/boards/<token>/jobs/<job_id>?questions=true
which returns something like:

    {
      "id": 12345,
      "title": "Software Engineer",
      "questions": [
        {
          "label": "First Name",
          "required": true,
          "fields": [
            {"name": "first_name", "type": "input_text", "values": []}
          ]
        },
        {
          "label": "Are you authorized to work in the US?",
          "required": true,
          "fields": [
            {"name": "question_123", "type": "multi_value_single_select_fields",
             "values": [{"value": 1, "label": "Yes"}, {"value": 0, "label": "No"}]}
          ]
        },
        ...
      ]
    }

Submit is a `POST` to the same resource with a multipart body. We don't
actually submit in the MVP — the executor is DRY_RUN by default, and
real submissions will live behind Playwright (some Greenhouse tenants
require csrf tokens that are easier to harvest via a browser session).
What we DO need, cleanly, is:

* fetch_form — hit the questions endpoint, map to FieldSpec list
* build_payload — produce the multipart dict the submit endpoint expects
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

import httpx

from autoapply.execute.base import Applicator, ApplyResult
from autoapply.execute.standard_fields import FieldSpec, ResolvedField
from autoapply.tracker.models import Job


log = logging.getLogger(__name__)


GH_BASE = "https://boards-api.greenhouse.io/v1/boards"


# -- Greenhouse field type mapping -----------------------------------------


def _kind_from_type(t: str) -> str:
    t = (t or "").lower()
    if t in ("input_file",):
        return "file"
    if t in ("textarea",):
        return "textarea"
    # Greenhouse API returns both "multi_value_single_select" (newer) and
    # "multi_value_single_select_fields" (older) — handle both.
    if t in (
        "multi_value_single_select",
        "multi_value_single_select_fields",
        "single_select",
    ):
        return "select"
    if t in (
        "multi_value_multi_select",
        "multi_value_multi_select_fields",
        "multi_select",
    ):
        return "multi_select"
    if t in ("input_hidden",):
        return "text"  # still resolve, but submit as hidden
    return "text"


def _question_to_specs(q: dict[str, Any]) -> Iterable[FieldSpec]:
    """One Greenhouse question can expose multiple sub-fields (e.g.,
    demographics compound fields). Yield one FieldSpec per sub-field."""
    label = str(q.get("label") or "").strip()
    required = bool(q.get("required") or False)
    fields = q.get("fields")
    if not isinstance(fields, list) or not fields:
        return
    for f in fields:
        if not isinstance(f, dict):
            continue
        name = str(f.get("name") or "")
        if not name:
            continue
        values = f.get("values") or []
        options = []
        if isinstance(values, list):
            for v in values:
                if isinstance(v, dict):
                    lbl = v.get("label")
                    if lbl:
                        options.append(str(lbl))
        yield FieldSpec(
            name=name,
            label=label,
            required=required,
            kind=_kind_from_type(str(f.get("type") or "")),
            options=options,
        )


# -- Applicator -------------------------------------------------------------


class GreenhouseApplicator(Applicator):
    name = "greenhouse"

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
        url = f"{GH_BASE}/{job.board_token}/jobs/{job.source_id}?questions=true"
        resp = self._client.get(url)
        resp.raise_for_status()
        data = resp.json()
        return list(self._parse_questions(data))

    def _parse_questions(self, data: dict[str, Any]) -> Iterable[FieldSpec]:
        qs = data.get("questions")
        if not isinstance(qs, list):
            return
        for q in qs:
            if isinstance(q, dict):
                yield from _question_to_specs(q)

    # ---- Payload build -------------------------------------------------

    def build_payload(self, job: Job, resolved: list[ResolvedField]) -> dict[str, Any]:
        """Build a multipart-ready dict. Real submission would pass this to
        `httpx.Client.post(url, data=..., files=...)`. Kept deterministic for
        DRY_RUN audit — the payload is what we'd actually send."""
        data: dict[str, Any] = {}
        files: dict[str, str] = {}
        for r in resolved:
            if r.source == "machine_key" and r.name in ("resume", "cover_letter"):
                if r.value:
                    files[r.name] = r.value
                continue
            if r.value:
                data[r.name] = r.value
        return {"data": data, "files": files, "job_id": job.source_id}

    # ---- Submit --------------------------------------------------------

    def submit(self, job: Job, payload: dict[str, Any]) -> ApplyResult:
        """Submit via Playwright (stealth browser).

        Called only when dry_run=False. Navigates
        boards.greenhouse.io/<token>/jobs/<id>, fills every resolved field,
        uploads the resume PDF, and clicks submit. Returns:
            outcome="ok"      — success confirmed on the post-submit page.
            outcome="captcha" — CAPTCHA wall encountered; job goes to review.
            outcome="failed"  — any other error.
        """
        from autoapply.config import get_settings
        from autoapply.execute.playwright_submit import (
            CaptchaDetected,
            SubmitFailed,
            submit_greenhouse,
        )

        settings = get_settings()
        # Package the LLM context so the Playwright driver can run the
        # Stage-2 DOM batch after the React SPA has re-rendered. The
        # answer-bank YAML is sent as raw text — the prompt wants both
        # keys and values visible to the model.
        try:
            bank_yaml_text = settings.answer_bank_path.read_text(encoding="utf-8")
        except Exception:
            bank_yaml_text = ""
        llm_context = {
            "profile": self.profile,
            "answer_bank_yaml": bank_yaml_text,
            "track": self.track,
            "company": job.company or "",
            "job_title": job.title or "",
        }
        try:
            result = submit_greenhouse(
                board_token=job.board_token,
                job_id=str(job.source_id),
                data=payload.get("data", {}),
                files=payload.get("files", {}),
                headless=True,
                imap_server=settings.IMAP_SERVER,
                imap_port=settings.IMAP_PORT,
                imap_email=settings.IMAP_EMAIL,
                imap_password=settings.IMAP_PASSWORD,
                imap_code_timeout=settings.IMAP_CODE_TIMEOUT,
                llm_context=llm_context,
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
