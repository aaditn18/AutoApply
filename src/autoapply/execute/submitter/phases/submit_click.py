"""Submit-click phase + post-submit CAPTCHA handling.

Clicks the submit button, waits briefly for navigation, and handles
any CAPTCHA that appears after the click. Two CAPTCHA paths:

- A solver is configured (``captcha_solver`` + ``captcha_solver_api_key``
  non-empty) → attempt to solve and retry the submit. On success we
  return normally.
- No solver OR solve failed → raise :class:`CaptchaDetected`; the
  caller routes the application to the review queue.

hCaptcha's challenge modal can take several seconds to render after
the submit click (async challenge fetch + image preload), so we poll
a short window instead of one-shot checking.
"""

from __future__ import annotations

import logging
from typing import Any

from .. import CaptchaDetected, SubmitFailed
from ..captcha_detect import wait_for_captcha
from ..captcha_retry import maybe_solve_and_retry_captcha
from ..util import jitter


log = logging.getLogger(__name__)


def click_submit_and_handle_captcha(
    page: Any,
    *,
    submit_selector: str,
    captcha_solver: str,
    captcha_solver_api_key: str,
    captcha_solver_timeout: int,
) -> None:
    """Click submit, wait for post-submit page, resolve any CAPTCHA.

    Raises :class:`SubmitFailed` if the submit button is not clickable
    (stale selector, DOM mutation between check and click).
    Raises :class:`CaptchaDetected` if a post-submit CAPTCHA can't be
    solved.
    """
    jitter(1.0, 3.0)

    try:
        page.click(submit_selector, timeout=10_000)
    except Exception as exc:
        raise SubmitFailed(
            f"Submit button not clickable: {exc}"
        ) from exc

    # Wait for post-submit page load. SPA may not trigger networkidle;
    # check content downstream.
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:
        pass
    jitter(1.0, 2.0)

    # Poll for captcha (challenge modal can lag several seconds after click).
    if wait_for_captcha(page, timeout=12.0):
        solved = maybe_solve_and_retry_captcha(
            page,
            submit_selector=submit_selector,
            solver=captcha_solver,
            api_key=captcha_solver_api_key,
            timeout=captcha_solver_timeout,
        )
        if not solved:
            raise CaptchaDetected("CAPTCHA appeared after submit attempt")
