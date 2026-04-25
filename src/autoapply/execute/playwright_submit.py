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
    "submit_ashby",
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
    llm_context: dict[str, Any] | None = None,
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
    # prior runs). Two complementary mechanisms handle them:
    #
    # 1. ``augmented_data`` below prepopulates guessed ``name``/``id``
    #    attributes on common variants (``city``, ``state``, ``zip``, …).
    #    If the element exists, it's filled; if not, it's skipped silently.
    # 2. ``label_values`` below drives the label-aware fallback pass in
    #    :mod:`.submitter.label_fallback`. That pass walks the DOM after
    #    ``augmented_data`` runs, reads each empty required field's visible
    #    label, classifies it via :func:`autoapply.answers.classifier.classify`,
    #    and fills the value matching the resulting ``QuestionType`` — so
    #    even SPA-injected fields with per-tenant random ids get filled as
    #    long as their label matches our classifier's location family.
    #
    # The overlay is safe: if any of these keys are already in ``data``
    # (from the resolver), those take precedence. The label fallback only
    # fires on empty fields — so it never clobbers a resolver-set value.
    augmented_data = {
        "country": "United States",
        "location": "College Park, MD",   # SPA "Location (City)*" input
        "city": "College Park",           # bare city input on some tenants
        "state": "MD",                    # bare state input
        "zip": "20740",                   # zip / postal code
        "postal_code": "20740",
        **data,
    }

    # Label-aware fallback: any field whose label classifies to one of
    # these QuestionType values (see autoapply.answers.types) will be
    # filled with the mapped value. Only atomic-location fields are
    # included — demographic / policy questions still route through the
    # bank + review path, never via this fallback.
    label_values = {
        "current_city": "College Park",
        "current_state": "MD",
        "current_zip": "20740",
        "current_location": "College Park, MD",
        "full_address": "8150 Baltimore Ave, Apt. 308-C, College Park, MD 20740",
        "street_address": "8150 Baltimore Ave",
        "address_line_2": "Apt. 308-C",
    }

    return submit_form(
        url=url,
        data=augmented_data,
        label_values=label_values,
        llm_context=llm_context,
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
    llm_context: dict[str, Any] | None = None,
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
        llm_context=llm_context,
    )


def submit_ashby(
    *,
    apply_url: str,
    data: dict[str, Any],
    files: dict[str, str],
    headless: bool = True,
    imap_server: str = "imap.gmail.com",
    imap_port: int = 993,
    imap_email: str = "",
    imap_password: str = "",
    imap_code_timeout: int = 90,
    llm_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fill and submit an Ashby-hosted application form via Playwright.

    Ashby's apply pages are SPA-driven — every tenant-specific question
    (EEO, essays, custom dropdowns) gets injected into the DOM at
    render time, not listed in a form-spec API. We rely entirely on
    Stage-2 DOM batch (``submitter/dom/``) to discover and fill those.
    The ``data`` dict coming in only carries the base fields (name,
    email, phone, LinkedIn) + any machine-keyed atoms our resolver
    knows about.

    Returns an outcome dict: ``{"ok": bool, "url": str, "error":
    str|None, "field_errors": list[str]}``. Raises
    :class:`CaptchaDetected` if a CAPTCHA wall is detected.
    """
    # Ashby's hosted apply URL is candidate-supplied (it came from the
    # public feed's ``applyUrl`` field), so we use it directly instead
    # of constructing one. A stray redirect from /application to the
    # same URL without path change is handled by success_detect.
    #
    # Reuse the same augmented_data / label_values strategy as Greenhouse
    # — Ashby forms frequently include location atoms (City / State /
    # Country) as separate required fields with varying DOM names. The
    # label-driven fallback picks them up even when the resolver didn't
    # emit a matching ``name`` key.
    augmented_data = {
        "country": "United States",
        "location": "College Park, MD",
        "city": "College Park",
        "state": "MD",
        "zip": "20740",
        "postal_code": "20740",
        **data,
    }
    # Ashby's hosted apply pages render LinkedIn / Website / "How did
    # you find out about us?" as plain text inputs with opaque
    # ``question_<numeric_id>`` attribute names — our machine-key
    # router can't match them, so they have to be filled via the
    # label-driven fallback. Add their canonical answers to the
    # label_values dict so ``label_fallback.fill_by_label`` picks
    # them up after the classifier identifies the QuestionType.
    linkedin_url = (
        str(data.get("linkedin_url") or "")
        or "https://www.linkedin.com/in/aadit-nilay/"
    )
    label_values = {
        "current_city": "College Park",
        "current_state": "MD",
        "current_zip": "20740",
        "current_location": "College Park, MD",
        "full_address": "8150 Baltimore Ave, Apt. 308-C, College Park, MD 20740",
        "street_address": "8150 Baltimore Ave",
        "address_line_2": "Apt. 308-C",
        # Ashby-text-field defaults — keyed by QuestionType.value so
        # ``classify(label).type.value`` lookups land on them.
        "linkedin_url": linkedin_url,
        "github_url": str(data.get("github_url") or "")
            or "https://github.com/aaditn18",
        "how_heard_about": "LinkedIn",
    }

    return submit_form(
        url=apply_url,
        data=augmented_data,
        label_values=label_values,
        llm_context=llm_context,
        files=files,
        headless=headless,
        # Ashby's hosted SPA renders the submit button without a stable
        # `type="submit"` attribute on many tenants — the default form
        # submit selectors miss it. Match by visible text "Submit
        # Application" (the literal Ashby label) with data-testid /
        # attribute fallbacks first in case a newer tenant uses them.
        # Playwright's ``:has-text`` locator matches inner text
        # case-insensitively.
        submit_selector=(
            "button[data-testid='submit-application'], "
            "button[data-ashby='submit'], "
            "button[type='submit'], "
            "input[type='submit'], "
            "button:has-text('Submit Application'), "
            "button:has-text('Submit application'), "
            "button:has-text('Submit'):not(:has-text('Submitting'))"
        ),
        success_url_fragments=(
            "confirmation",
            "thank",
            "success",
            "submitted",
            "application-submitted",
            "/submitted",
        ),
        imap_server=imap_server,
        imap_port=imap_port,
        imap_email=imap_email,
        imap_password=imap_password,
        imap_code_timeout=imap_code_timeout,
    )
