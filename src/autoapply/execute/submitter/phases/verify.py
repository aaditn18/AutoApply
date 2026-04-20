"""Success-detection + post-submit diagnostic phase.

After submit (and any CAPTCHA / OTP resolution), we check whether the
page landed on a confirmation. The success detector is tenant-aware:
it checks URL fragments (``success_url_fragments``) plus page-content
markers. On failure we log the first 600 chars of page text so
debugging a miss-detected success doesn't require re-running.

Lever is the original debugging target (silent server-side rejections
with no error banner), so we also save a post-submit screenshot + dump
iframe info when a Lever submit fails. Greenhouse failures don't get
this extra dump — their failure modes are more self-evident in the page
body.
"""

from __future__ import annotations

import logging
from typing import Any

from ..diagnostics import dump_post_submit_failure
from ..success_detect import detect_submit_success
from ..util import inner_text_safe


log = logging.getLogger(__name__)


def check_submit_success(
    page: Any,
    *,
    url: str,
    success_url_fragments: tuple[str, ...],
    submit_selector: str,
) -> tuple[bool, str | None]:
    """Return ``(ok, error_reason)`` for the post-submit page state.

    ``error_reason`` is ``None`` on success; otherwise it's the reason
    string the detector returned, suitable for storing in
    ``Application.outcome_reason``.

    Side effects: logs page text on failure, dumps a Lever post-submit
    screenshot on Lever failures.
    """
    success, reason = detect_submit_success(
        page,
        success_url_fragments,
        submit_selector=submit_selector,
    )

    if success:
        log.info("submit success (%s): %s", reason, page.url)
        return True, None

    log.warning("submit appears unsuccessful for %s: %s", url, reason)
    try:
        page_text = inner_text_safe(page).lower()
        log.info(
            "post-submit page text (first 600 chars): %s",
            page_text[:600],
        )
    except Exception:
        pass

    # Lever-only: extra diagnostic dump (screenshot + iframe log).
    if "jobs.lever.co" in url:
        dump_post_submit_failure(page, url)

    return False, reason
