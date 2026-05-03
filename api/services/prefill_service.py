"""Open a non-headless Playwright browser, navigate to the apply URL
of a failed Application, fill every field with the answers we already
have on file, and leave the window open so the user only has to click
Submit themselves.

This is the "open prefilled in a real browser" path used by the local
web UI when an application failed to a captcha wall, an Ashby
spam-flag block, or a review_flags row that the user wants to handle
manually instead of re-running the full apply pipeline.

Why reuse the stored ``Application.answers`` instead of re-running the
resolver:
  - The user already paid the LLM cost on the first attempt.
  - The original answers are what the user "approved" implicitly by
    not editing them; running the resolver again risks getting
    different answers (LLM nondeterminism).
  - Faster — skips the entire fetch_form + resolve_all_batched stack.

Lifecycle:
  1. POST /api/applications/{id}/prefill → start_prefill_for_application
  2. We look up the Application + Job, build the same payload that
     the original submitter saw (data dict + files dict).
  3. Spawn a daemon thread that calls the source-specific submit_X()
     with ``stop_before_submit=True, headless=False``.
  4. Return immediately with ``{"ok": True, "job_id": ...}``.
  5. Submitter runs phases 1-7 (navigate, upload, fill, EEO,
     auto-consent), then blocks on ``page.wait_for_event("close")``.
  6. User closes the window when they're done — the thread exits.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

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
        # Skip file paths — files dict is built separately. The
        # answer dict from older runs sometimes contains a "resume"
        # key holding a path; ignore it (we re-resolve from track).
        if k in ("resume", "cover_letter") and isinstance(v, str) and (
            v.startswith("/") or v.endswith(".pdf")
        ):
            continue
        data[k] = v

    if a.cover_letter_text:
        data["cover_letter"] = a.cover_letter_text

    files = {"resume": _resume_path_for_track(a.track_submitted)}
    return data, files


def _run_prefill_in_thread(
    *,
    source: str,
    job: Job,
    data: dict,
    files: dict,
) -> None:
    """Worker thread — calls the source-specific submit_X function.

    Errors are logged but don't propagate (this thread is detached).
    """
    settings = get_settings()
    common = {
        "data": data,
        "files": files,
        "headless": False,            # user must SEE the window
        "imap_server": settings.IMAP_SERVER,
        "imap_port": settings.IMAP_PORT,
        "imap_email": settings.IMAP_EMAIL,
        "imap_password": settings.IMAP_PASSWORD,
        "imap_code_timeout": settings.IMAP_CODE_TIMEOUT,
    }

    # Build llm_context the same way the applicators do — Stage-2 DOM
    # batch may still need to fill late-bound fields the original
    # apply attempt didn't capture in answers.
    try:
        bank_yaml_text = settings.answer_bank_path.read_text(encoding="utf-8")
    except Exception:
        bank_yaml_text = ""
    llm_context = {
        "answer_bank_yaml": bank_yaml_text,
        "track": job.track or "swe",
        "company": job.company or "",
        "job_title": job.title or "",
    }

    try:
        from autoapply.execute.submitter.driver import submit_form
    except Exception as exc:
        log.exception("prefill: failed to import submitter: %s", exc)
        return

    # Per-source URL + selector + success-fragment overrides — same
    # values the existing submit_greenhouse/lever/ashby wrappers use.
    if source == "greenhouse":
        url = (
            f"https://boards.greenhouse.io/{job.board_token}/jobs/{job.source_id}"
        )
        submit_selector = "button[type='submit'], input[type='submit']"
    elif source == "lever":
        url = job.url if "/apply" in job.url else f"{job.url.rstrip('/')}/apply"
        submit_selector = "button[type='submit'], input[type='submit']"
    elif source == "ashby":
        url = job.url
        submit_selector = (
            "button[data-testid='submit-application'], "
            "button[data-ashby='submit'], "
            "button[type='submit'], "
            "input[type='submit'], "
            "button:has-text('Submit Application')"
        )
    else:
        log.error("prefill: unknown source %r for job %s", source, job.id)
        return

    try:
        submit_form(
            url=url,
            submit_selector=submit_selector,
            # The fill phases never read these in prefill mode — we
            # block on page-close before phase 8 (submit). Pass a
            # generic set so the call signature is satisfied.
            success_url_fragments=("confirmation", "thank", "success", "submitted"),
            llm_context=llm_context,
            stop_before_submit=True,
            **common,
        )
    except Exception as exc:
        log.exception("prefill: submitter failed for job %s: %s", job.id, exc)


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
            "job": _DetachedJob(job),
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
