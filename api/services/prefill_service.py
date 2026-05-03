"""Open a non-headless Playwright browser, navigate to the apply URL
of a failed Application, fill every field with the answers we already
have on file, and leave the window open so the user only has to click
Submit themselves.

This is the "open prefilled in a real browser" path used by the local
web UI when an application failed to a captcha wall, an Ashby
spam-flag block, or a review_flags field that the user wants to
handle manually instead of re-running the full apply pipeline.

Implementation
--------------

We delegate to the *exact same* per-source wrappers that the
applicators use (``submit_greenhouse`` / ``submit_lever`` /
``submit_ashby``), with one new pass-through kwarg
``stop_before_submit=True`` that the wrappers forward to
``submitter.driver.submit_form``. This guarantees the prefill path
gets every per-tenant quirk the wrappers already encode:

  - Greenhouse SPA's ``augmented_data`` overlay (country / location /
    city / state / zip / postal_code) so atomic-location inputs that
    aren't on the resolved-keys list still fill.
  - Greenhouse + Ashby's ``label_values`` for the label-driven
    fallback in ``submitter.label_fallback`` (LinkedIn / GitHub /
    "How did you hear about us?" / address atoms).
  - Ashby's submit-button selector list and success URL fragments.
  - Lever's hCaptcha-accessibility cookie pre-injection.
  - Captcha solver credentials when configured.

Crucially, ``llm_context`` carries the **full** Profile object plus
the answer-bank YAML and track, so Stage-2 DOM batch can pull
education / EEO / current-location fields the original failed
attempt didn't capture in ``Application.answers``.

Lifecycle
---------

  1. POST /api/applications/{id}/prefill → start_prefill_for_application
  2. Look up the Application + Job, build (data, files) from the
     stored answers + cover letter + per-track resume PDF.
  3. Snapshot the Job and the resolved Profile (the request session
     will close before the worker thread runs Playwright; ORM
     attributes would 404 mid-thread).
  4. Spawn a daemon thread that calls the source-specific submit_X()
     with ``stop_before_submit=True, headless=False``.
  5. Return immediately with metadata.
  6. Submitter runs phases 1-7 (navigate, upload, fill, EEO,
     auto-consent), then blocks on ``page.wait_for_event("close")``.
  7. User closes the window when they're done; the thread exits.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from autoapply.config import get_settings
from autoapply.tracker.models import Application, Job

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESUMES_DIR = PROJECT_ROOT / "resumes"


def _resume_path_for_track(track: str | None) -> str:
    """Resolve the per-track resume PDF the submitter should upload.

    Falls back to ``resume_swe.pdf`` when the track is None or a PDF
    for that track doesn't exist.
    """
    track_lower = (track or "swe").lower().strip()
    candidate = RESUMES_DIR / f"aadit_nilay_resume_{track_lower}.pdf"
    if candidate.exists():
        return str(candidate)
    fallback = RESUMES_DIR / "aadit_nilay_resume_swe.pdf"
    return str(fallback)


def _build_payload(a: Application) -> tuple[dict, dict]:
    """Reconstruct (data, files) from a failed Application row.

    The submitter expects:
      - ``data``: every textual answer keyed by question name
      - ``files``: ``{"resume": "/path/to/resume.pdf",
                      "cover_letter"?: "/path/to/coverletter.pdf"}``

    We pull ``data`` straight from ``Application.answers`` minus any
    keys that look like file paths (``resume`` / ``cover_letter``).
    Cover letter text was stored separately in
    ``Application.cover_letter_text`` and is re-injected into ``data``
    so the submitter's textarea fill picks it up.
    """
    data: dict = {}
    for k, v in (a.answers or {}).items():
        # Skip file paths — files dict is built separately.
        if k in ("resume", "cover_letter") and isinstance(v, str) and (
            v.startswith("/") or v.endswith(".pdf")
        ):
            continue
        data[k] = v

    if a.cover_letter_text:
        data["cover_letter"] = a.cover_letter_text

    files = {"resume": _resume_path_for_track(a.track_submitted)}
    return data, files


def _load_llm_context(track: str, company: str, job_title: str) -> dict[str, Any]:
    """Build the full llm_context that Stage-2 DOM batch expects.

    Includes:
      - ``profile``: the Profile object for the track (NEEDED for
        Stage-2 to resolve education / EEO / current-location atoms
        that weren't in the original Application.answers dict).
      - ``answer_bank_yaml``: raw YAML so the prompt can show the
        bank to the LLM verbatim.
      - ``track`` / ``company`` / ``job_title``: prompt context.
    """
    from autoapply.profile.build import load_profiles

    settings = get_settings()
    try:
        bank_yaml_text = settings.answer_bank_path.read_text(encoding="utf-8")
    except Exception:
        bank_yaml_text = ""

    profile = None
    try:
        profiles = load_profiles(settings.profile_json_path)
        profile = profiles.get(track) or profiles.get("swe")
    except Exception as exc:
        log.warning(
            "prefill: profile.json not loadable (%s) — Stage-2 DOM "
            "batch will run without the full profile context",
            exc,
        )

    return {
        "profile": profile,
        "answer_bank_yaml": bank_yaml_text,
        "track": track,
        "company": company,
        "job_title": job_title,
    }


def _run_prefill_in_thread(
    *,
    source: str,
    job_snapshot: "_DetachedJob",
    track: str,
    data: dict,
    files: dict,
) -> None:
    """Worker thread — calls the source-specific submit_X function.

    Errors are logged but don't propagate (this thread is detached).
    """
    settings = get_settings()
    llm_context = _load_llm_context(
        track=track,
        company=job_snapshot.company,
        job_title=job_snapshot.title,
    )

    try:
        from autoapply.execute.playwright_submit import (
            submit_ashby,
            submit_greenhouse,
            submit_lever,
        )
    except Exception as exc:
        log.exception("prefill: failed to import submitters: %s", exc)
        return

    common = dict(
        data=data,
        files=files,
        headless=False,
        imap_server=settings.IMAP_SERVER,
        imap_port=settings.IMAP_PORT,
        imap_email=settings.IMAP_EMAIL,
        imap_password=settings.IMAP_PASSWORD,
        imap_code_timeout=settings.IMAP_CODE_TIMEOUT,
        llm_context=llm_context,
        stop_before_submit=True,
    )

    try:
        if source == "greenhouse":
            submit_greenhouse(
                board_token=job_snapshot.board_token,
                job_id=str(job_snapshot.source_id),
                **common,
            )
        elif source == "lever":
            # Lever's wrapper rebuilds the URL from token + posting_id.
            # Job.source_id holds the posting id; Job.board_token holds
            # the company token.
            submit_lever(
                token=job_snapshot.board_token,
                posting_id=str(job_snapshot.source_id),
                hcaptcha_accessibility_token=getattr(
                    settings, "HCAPTCHA_ACCESSIBILITY_TOKEN", ""
                ) or "",
                captcha_solver=getattr(settings, "CAPTCHA_SOLVER", "") or "",
                captcha_solver_api_key=getattr(
                    settings, "CAPTCHA_SOLVER_API_KEY", ""
                ) or "",
                captcha_solver_timeout=int(
                    getattr(settings, "CAPTCHA_SOLVER_TIMEOUT", 180) or 180
                ),
                **common,
            )
        elif source == "ashby":
            submit_ashby(
                apply_url=job_snapshot.url,
                **common,
            )
        else:
            log.error(
                "prefill: unknown source %r for job %s",
                source, job_snapshot.id,
            )
    except Exception as exc:
        log.exception(
            "prefill: submitter failed for job %s: %s",
            job_snapshot.id, exc,
        )


def start_prefill_for_application(s: Session, app_id: int) -> dict:
    """Public entry point — non-blocking. Returns immediately with
    metadata; the actual browser session runs in a daemon thread.

    Raises :class:`LookupError` if the application doesn't exist.
    Raises :class:`ValueError` if the application has no stored
    answers (nothing to replay).
    """
    a = s.get(Application, app_id)
    if a is None:
        raise LookupError(f"application {app_id} not found")
    job = s.get(Job, a.job_id)
    if job is None:
        raise LookupError(f"job {a.job_id} (parent of app {app_id}) not found")

    if not a.answers:
        raise ValueError(
            f"application {app_id} has no stored answers — nothing to replay"
        )

    data, files = _build_payload(a)

    log.info(
        "prefill: starting for app=%s job=%s source=%s url=%s",
        app_id, job.id, job.source, job.url,
    )

    t = threading.Thread(
        target=_run_prefill_in_thread,
        kwargs={
            "source": job.source,
            "job_snapshot": _DetachedJob(job),
            "track": a.track_submitted or job.track or "swe",
            "data": data,
            "files": files,
        },
        daemon=True,
        name=f"prefill-{app_id}",
    )
    t.start()

    return {
        "ok": True,
        "application_id": app_id,
        "job_id": job.id,
        "source": job.source,
        "url": job.url,
        "fields_count": len(data),
        "resume_path": files.get("resume", ""),
    }


class _DetachedJob:
    """Snapshot of Job columns we need in the worker thread.

    The session that produced ``job`` will close before the thread
    runs the playwright dance; ORM attributes would 404. Copy the
    fields we read into a plain object.
    """

    __slots__ = (
        "id", "source", "board_token", "source_id", "url",
        "title", "company", "track",
    )

    def __init__(self, job: Job):
        self.id = job.id
        self.source = job.source
        self.board_token = job.board_token
        self.source_id = job.source_id
        self.url = job.url
        self.title = job.title
        self.company = job.company
        self.track = job.track
