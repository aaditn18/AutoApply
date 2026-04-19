"""Public entry points for Playwright-driven form submission.

This module is a thin shim: it wraps :mod:`autoapply.execute.submitter`
(the split implementation) with the two ATS-specific public entry points
our applicators call, plus the exception types every caller imports.

The real mechanics — browser setup, field filling, file upload, Lever
qualifying cards, IMAP OTP fetch, captcha detect/retry, success detection,
and diagnostic dumps — all live in :mod:`.submitter` submodules. See that
package's ``__init__.py`` for a module-by-module index.

Outcome dict for both entry points::

    {"ok": bool, "url": str, "error": str | None, "field_errors": list[str]}

Raises:
    CaptchaDetected — a CAPTCHA wall was hit that our solver could not pass;
                      the caller should route the application to the review
                      queue.
    SubmitFailed    — unrecoverable browser / network / OTP error.
"""

from __future__ import annotations

import logging
from typing import Any

# Re-export exceptions from the submitter package so existing callers
# (``from autoapply.execute.playwright_submit import CaptchaDetected``)
# keep working without change.
from .submitter import CaptchaDetected, SubmitFailed
from .submitter.driver import submit_form


log = logging.getLogger(__name__)


__all__ = [
    "CaptchaDetected",
    "SubmitFailed",
    "submit_greenhouse",
    "submit_lever",
]


def submit_greenhouse(
    *,
    board_token: str,
    job_id: str,
    data: dict[str, Any],
    files: dict[str, str],
    headless: bool = True,
    imap_server: str = "imap.gmail.com",
    imap_port: int = 993,
    imap_email: str = "",
    imap_password: str = "",
    imap_code_timeout: int = 90,
) -> dict[str, Any]:
    """Fill and submit a Greenhouse application form via Playwright.

    Returns an outcome dict: ``{"ok": bool, "url": str, "error": str|None,
    "field_errors": list[str]}``.
    Raises :class:`CaptchaDetected` if a CAPTCHA wall is detected.

    If the Greenhouse board requires email verification (an OTP sent to the
    applicant's address), the code is fetched automatically via IMAP when
    ``imap_email`` + ``imap_password`` are provided.
    """
    url = f"https://boards.greenhouse.io/{board_token}/jobs/{job_id}"

    # The new job-boards.greenhouse.io SPA renders several applicant-info
    # inputs that are NOT part of the API ``questions`` list — we detected
    # these at runtime (Fanatics, Smartsheet, and others failed on them in
    # prior runs). Inject likely values BEFORE ``submit_form`` so the
    # generic ``fill_field(page, name, value)`` pass picks them up via
    # ``[name="..."]`` / ``[id="..."]`` match.
    #
    # Safe overlay: if any of these keys are already in ``data`` (from the
    # resolver), those take precedence. If the DOM doesn't have the
    # element (older ``boards.greenhouse.io`` route), ``fill_field`` just
    # records a ``fill:<name>:ValueError`` in ``field_errors`` — harmless.
    augmented_data = {
        "country": "United States",
        "location": "College Park, MD",   # SPA "Location (City)*" input
        "city": "College Park",           # bare city input on some tenants
        "state": "MD",                    # bare state input
        "zip": "20740",                   # zip / postal code
        "postal_code": "20740",
        **data,
    }

    return submit_form(
        url=url,
        data=augmented_data,
        files=files,
        headless=headless,
        # Old boards.greenhouse.io used #submit_app.
        # New job-boards.greenhouse.io SPA uses a button with text
        # "Submit Application". We try all three.
        submit_selector=(
            "#submit_app, "
            "button[data-qa='submit-app-button'], "
            'button[type="submit"], '
            'input[type="submit"]'
        ),
        success_url_fragments=("confirmation", "thank", "success", "submitted"),
        imap_server=imap_server,
        imap_port=imap_port,
        imap_email=imap_email,
        imap_password=imap_password,
        imap_code_timeout=imap_code_timeout,
    )


def submit_lever(
    *,
    token: str,
    posting_id: str,
    data: dict[str, Any],
    files: dict[str, str],
    headless: bool = True,
    hcaptcha_accessibility_token: str = "",
    imap_server: str = "imap.gmail.com",
    imap_port: int = 993,
    imap_email: str = "",
    imap_password: str = "",
    imap_code_timeout: int = 90,
    captcha_solver: str = "",
    captcha_solver_api_key: str = "",
    captcha_solver_timeout: int = 180,
) -> dict[str, Any]:
    """Fill and submit a Lever application form via Playwright.

    ``hcaptcha_accessibility_token`` — if provided (register once at
    ``https://dashboard.hcaptcha.com/signup?type=accessibility``), it is
    injected as the ``hc_accessibility`` cookie on the hcaptcha.com domain
    before navigating to the apply page. This causes hCaptcha to issue a
    silent pass token without showing a challenge to the headless browser.

    ``imap_*`` — credentials for fetching the email verification code that
    some Lever boards send after the initial form submit (same OTP flow
    as Greenhouse).

    ``captcha_solver`` / ``captcha_solver_api_key`` — when set, any detected
    hCaptcha is forwarded to the configured solver (see
    :mod:`autoapply.execute.captcha_solver` + :mod:`.captcha_coords`).
    """
    url = f"https://jobs.lever.co/{token}/{posting_id}/apply"

    # Build the pre-navigation cookie list for hCaptcha bypass.
    pre_cookies: list[dict[str, Any]] = []
    if hcaptcha_accessibility_token:
        pre_cookies.append({
            "name": "hc_accessibility",
            "value": hcaptcha_accessibility_token,
            "domain": ".hcaptcha.com",
            "path": "/",
            "httpOnly": False,
            "secure": True,
            "sameSite": "None",
        })
    else:
        log.warning(
            "submit_lever: HCAPTCHA_ACCESSIBILITY_TOKEN not set — "
            "Lever's hCaptcha widget may block submission. Register once at "
            "https://dashboard.hcaptcha.com/signup?type=accessibility, then "
            "copy the hc_accessibility cookie value into .env."
        )

    return submit_form(
        url=url,
        data=data,
        files=files,
        headless=headless,
        # Lever's visible submit button triggers hCaptcha; the hidden
        # #hcaptchaSubmitBtn is what actually submits the form after the
        # challenge succeeds. With the accessibility cookie, hCaptcha
        # executes silently and #hcaptchaSubmitBtn is clicked automatically.
        # We click #btn-submit (the visible one) — the JS handles the rest.
        submit_selector="#btn-submit",
        success_url_fragments=("confirmation", "thank", "success", "submitted"),
        pre_navigation_cookies=pre_cookies,
        imap_server=imap_server,
        imap_port=imap_port,
        imap_email=imap_email,
        imap_password=imap_password,
        imap_code_timeout=imap_code_timeout,
        captcha_solver=captcha_solver,
        captcha_solver_api_key=captcha_solver_api_key,
        captcha_solver_timeout=captcha_solver_timeout,
    )
