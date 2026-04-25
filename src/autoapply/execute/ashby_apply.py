"""Ashby applicator — browser-automation-only path.

Unlike Greenhouse and Lever, Ashby's public API exposes **only a job
listing feed** at ``GET /posting-api/job-board/{clientname}``. The
form-question schema (returned by the authenticated ``jobPosting.info``
endpoint as ``applicationFormDefinition``) and the submission endpoint
(``applicationForm.submit``) both require a per-customer API key
scoped with ``jobsRead`` + ``candidatesWrite``. A cross-company portal
can't realistically obtain those — no OAuth / partner-token path
exists (unlike Greenhouse Harvest).

Consequences for this applicator:

1. :meth:`fetch_form` returns a small static :data:`_BASE_FIELDS` list
   covering the fields the Ashby hosted SPA universally asks for
   (resume upload, name, email, phone, optional LinkedIn / cover
   letter). It makes **no HTTP call** — there's no endpoint to hit.
2. The real form discovery + fill happens at submit time via the
   existing Stage-2 DOM batch (``execute/submitter/dom/``). Any
   tenant-specific question (EEO, citizenship, essays, dropdowns) is
   scraped from the rendered apply page and routed through the
   batched-LLM resolver the same way Greenhouse SPA-injected fields
   already are.
3. :meth:`submit` delegates to :func:`submit_ashby` in
   ``playwright_submit.py``, which composes the existing submitter
   phases for a hosted Ashby apply URL.

The base-field ``name`` attributes below use the same plain tokens as
Lever's ``_BASE_FIELDS`` (``name``, ``email``, ``phone``, ``resume``,
``linkedin_url``, ``cover_letter``). Two reasons:

1. Those names match the machine-key rules in
   ``state/rules/machine_keys.yml`` so Phase-1 resolution populates
   them from the Profile without needing LLM help.
2. The fillers (``fill_field`` + ``file_input_selector``) search for
   DOM elements by multiple attributes (``name``, ``id``, aliases)
   and the label-driven fallback covers location atoms by classifier
   output, so the exact DOM attribute Ashby uses
   (``_systemfield_resume`` etc.) doesn't need to appear in the
   FieldSpec name. Stage-2 DOM batch handles everything else.
"""

from __future__ import annotations

import logging
from typing import Any

from autoapply.config import get_settings
from autoapply.execute.base import Applicator, ApplyResult
from autoapply.execute.standard_fields import FieldSpec, ResolvedField
from autoapply.tracker.models import Job


log = logging.getLogger(__name__)


# -- Standard fields Ashby apply forms typically expose -------------------


_BASE_FIELDS: list[FieldSpec] = [
    FieldSpec(name="name", label="Full Name", required=True, kind="text"),
    FieldSpec(name="email", label="Email", required=True, kind="text"),
    FieldSpec(name="phone", label="Phone", required=False, kind="text"),
    FieldSpec(name="resume", label="Resume / CV", required=True, kind="file"),
    FieldSpec(
        name="linkedin_url", label="LinkedIn URL", required=False, kind="text",
    ),
    FieldSpec(
        name="cover_letter", label="Cover Letter", required=False, kind="file",
    ),
]


# -- Applicator -----------------------------------------------------------


class AshbyApplicator(Applicator):
    name = "ashby"

    # Ashby's hosted apply pages don't require a per-request HTTP client
    # on our side — ``fetch_form`` is static, and ``submit`` hands off to
    # Playwright. Constructor matches the base ``Applicator`` signature
    # exactly; no extra kwargs.

    def close(self) -> None:
        """No-op close to match the Greenhouse / Lever applicator
        interface. The bulk scripts (``apply_best_per_company.py``)
        always call ``applicator.close()`` in a ``finally`` block."""
        return None

    # ---- Form fetch ----------------------------------------------------

    def fetch_form(self, job: Job) -> list[FieldSpec]:
        """Return the minimum field set.

        No HTTP call — the public ``posting-api/job-board`` feed used
        for ingestion doesn't expose a form schema, and the
        authenticated ``jobPosting.info`` endpoint that does is gated
        behind a per-customer API key we don't have. Stage-2 DOM batch
        discovers tenant-specific fields at submit time.
        """
        return list(_BASE_FIELDS)

    # ---- Payload build -------------------------------------------------

    def build_payload(self, job: Job, resolved: list[ResolvedField]) -> dict[str, Any]:
        """Partition resolved fields into ``data`` + ``files`` dicts.

        Shape matches Greenhouse / Lever applicators so downstream
        dry-run dump + DB audit code is uniform.
        """
        data: dict[str, Any] = {}
        files: dict[str, str] = {}
        for r in resolved:
            if r.source == "machine_key" and r.name in ("resume", "cover_letter"):
                if r.value:
                    files[r.name] = r.value
                continue
            if r.value:
                data[r.name] = r.value
        return {
            "data": data,
            "files": files,
            "apply_url": job.url,
        }

    # ---- Submit --------------------------------------------------------

    def submit(self, job: Job, payload: dict[str, Any]) -> ApplyResult:
        """Submit via Playwright against ``job.url`` (the Ashby applyUrl).

        Called only when ``dry_run=False``. Outcome map matches
        Greenhouse/Lever applicators (``ok`` / ``captcha`` / ``failed``).
        """
        from autoapply.execute.playwright_submit import (
            CaptchaDetected,
            SubmitFailed,
            submit_ashby,
        )

        settings = get_settings()
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
            result = submit_ashby(
                apply_url=str(payload.get("apply_url") or job.url),
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
