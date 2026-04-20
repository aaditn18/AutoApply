"""File-upload phase + Lever "resume analysis" wait.

Ordering: resume and cover-letter upload MUST happen before any text
fill. Lever's React upload widget parses the PDF asynchronously and
then re-renders the form with pre-populated fields (name, email,
phone). Filling text fields before upload leads to Lever's re-render
clearing them. Upload → wait for analysis → fill text.

The ``cover_letter`` value can arrive either as a file path OR as raw
text content (when the generator produced plain text rather than a
PDF). In the text case we write it to a temp .txt file, upload, and
clean up before returning.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any

from ..file_upload import file_input_selector, upload_file
from ..util import jitter


log = logging.getLogger(__name__)


def upload_files(
    page: Any,
    files: dict[str, str],
    field_errors: list[str],
) -> None:
    """Iterate ``files`` and upload each via Playwright.

    Writes any non-path ``cover_letter`` text to a temp file first.
    Every failure is appended to ``field_errors`` as a tagged string
    (``missing_file:<name>``, ``no_input:<name>``, ``upload:<name>:<cls>``)
    so the caller can report what went wrong without aborting the whole
    submission.
    """
    tmp_files_to_delete: list[str] = []
    for name, path in files.items():
        if not path:
            continue

        abs_path = Path(path)
        if not abs_path.exists():
            # cover_letter as raw text → write to a temp .txt file so
            # Playwright can upload it.
            if name == "cover_letter" and len(path) > 20:
                tmp = tempfile.NamedTemporaryFile(
                    mode="w", suffix=".txt", delete=False, encoding="utf-8"
                )
                tmp.write(path)
                tmp.close()
                abs_path = Path(tmp.name)
                tmp_files_to_delete.append(tmp.name)
                log.debug("saved cover letter text to temp file: %s", abs_path)
            else:
                log.warning("file not found for upload field=%r path=%s", name, abs_path)
                field_errors.append(f"missing_file:{name}")
                continue

        # Greenhouse new job-boards SPA uses id= not name= on inputs.
        selector = file_input_selector(page, name)
        if not selector:
            log.debug("no file input found for field=%r; skipping", name)
            field_errors.append(f"no_input:{name}")
            continue

        try:
            upload_file(page, selector, str(abs_path))
            log.debug("uploaded %s → %s (selector=%r)", abs_path.name, name, selector)
            jitter(0.5, 1.5)
        except Exception as exc:
            log.debug("upload error field=%r: %s", name, exc)
            field_errors.append(f"upload:{name}:{type(exc).__name__}")

    for tmp_path in tmp_files_to_delete:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass


def wait_for_resume_analysis(page: Any, timeout_ms: int = 20_000) -> None:
    """Block until Lever's "Analyzing resume..." banner disappears.

    Lever parses the uploaded PDF server-side and shows
    "Analyzing resume…" while the parse is running. If we start filling
    text fields before the parse completes, Lever's post-analysis
    re-render wipes our fills. A short jitter follows the wait so the
    form settles before the next phase starts typing.

    Timeout is soft: we log and continue rather than raising, because
    some tenants never show the banner at all. The worst case is that
    a fill gets overwritten and the success detector catches the blank
    downstream.
    """
    try:
        page.wait_for_function(
            "() => !document.body.innerText.toLowerCase().includes('analyzing resume')",
            timeout=timeout_ms,
        )
        log.debug("resume analysis complete")
        jitter(0.5, 1.0)
    except Exception:
        log.debug("timed out waiting for resume analysis; proceeding anyway")
