"""The orchestrator — one function that drives a single form submission.

:func:`submit_form` composes named phase functions from
:mod:`.phases`. Each phase owns ONE concern:

    phases.browser.launch_browser_context   — Chromium + stealth
    phases.upload.upload_files              — file uploads
    phases.upload.wait_for_resume_analysis  — Lever "analyzing…" wait
    phases.api_fill.fill_api_fields         — iterate data dict
    lever_cards.fill_lever_cards            — Lever-only dynamic cards
    phases.stage2.run_stage2_batch          — DOM scrape + batch LLM
    label_fallback.fill_by_label            — label-aware safety net
    phases.submit_click.click_submit_and_handle_captcha
    phases.verification.handle_email_verification  — OTP
    phases.verify.check_submit_success      — final detect + logging

This module itself only owns the composition — opening the Playwright
context manager, routing between phases, and the outcome dict.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from . import CaptchaDetected, SubmitFailed
from .captcha_detect import detect_captcha
from .diagnostics import dump_pre_submit_state
from .imap_otp import detect_email_verification
from .label_fallback import fill_by_label
from .lever_cards import fill_lever_cards
from .success_detect import detect_submit_success
from .util import jitter
from .phases.browser import USER_AGENTS, launch_browser_context
from .phases.upload import upload_files, wait_for_resume_analysis
from .phases.api_fill import fill_api_fields
from .phases.stage2 import run_stage2_batch
from .phases.verification import handle_email_verification
from .phases.submit_click import click_submit_and_handle_captcha
from .phases.verify import check_submit_success


log = logging.getLogger(__name__)

# Re-exported for backwards compatibility with tests that import USER_AGENTS
# directly from this module. The canonical home is now phases.browser.
__all__ = ["USER_AGENTS", "submit_form"]


def submit_form(
    *,
    url: str,
    data: dict[str, Any],
    files: dict[str, str],
    headless: bool,
    submit_selector: str,
    success_url_fragments: tuple[str, ...],
    pre_navigation_cookies: list[dict[str, Any]] | None = None,
    imap_server: str = "imap.gmail.com",
    imap_port: int = 993,
    imap_email: str = "",
    imap_password: str = "",
    imap_code_timeout: int = 90,
    captcha_solver: str = "",
    captcha_solver_api_key: str = "",
    captcha_solver_timeout: int = 180,
    label_values: dict[str, str] | None = None,
    llm_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Drive a single Playwright-backed form submit.

    Returns ``{"ok": bool, "url": str, "error": str|None, "field_errors": list[str]}``.

    Raises :class:`CaptchaDetected` if a blocking captcha wall can't be
    solved (or no solver is configured) — caller routes the app to review.
    Raises :class:`SubmitFailed` on unrecoverable browser / navigation /
    OTP-retrieval errors.
    """
    from playwright.sync_api import sync_playwright

    field_errors: list[str] = []

    with sync_playwright() as p:
        browser, ctx, page = launch_browser_context(
            p, headless=headless, pre_navigation_cookies=pre_navigation_cookies,
        )
        try:
            # ── Phase 1: navigate ────────────────────────────────────────
            log.info("playwright: navigating to %s", url)
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            jitter(1.0, 2.5)

            # Abort if CAPTCHA is immediately visible on the form page.
            if detect_captcha(page):
                raise CaptchaDetected(f"CAPTCHA on form page: {url}")

            # ── Phase 2: upload files + wait for Lever resume analysis ──
            # Must precede fill; Lever's re-render clears text on analysis.
            upload_files(page, files, field_errors)
            if files:
                wait_for_resume_analysis(page)

            # ── Phase 3: fill API-sourced fields ────────────────────────
            fill_api_fields(page, data, field_errors)

            # ── Phase 4: Lever-only qualifying-card questions ───────────
            # Lever qualifying questions appear in the DOM as
            # input[name="cards[UUID][fieldN]"] but are NOT returned by
            # the public posting API — we discover them at runtime.
            if "jobs.lever.co" in url:
                fill_lever_cards(page, set(data.keys()), field_errors)

            # ── Phase 5: Stage-2 DOM batch LLM ──────────────────────────
            # Scrapes SPA-injected required fields (new
            # job-boards.greenhouse.io), resolves via ONE Gemini call,
            # fills them. Runs BEFORE label_fallback so the LLM gets
            # first crack at novel fields.
            run_stage2_batch(
                page,
                llm_context=llm_context,
                already_filled_keys=set(data.keys()),
            )

            # ── Phase 6: label-aware deterministic fallback ─────────────
            # Safety net for the small set of location-atom fields where
            # we have hardcoded answers — used when the LLM batch is
            # rate-limited or returns needs_review.
            if label_values:
                fill_by_label(page, label_values, set(data.keys()), field_errors)

            # ── Phase 7: pre-submit diagnostic dump ─────────────────────
            # Lever + Greenhouse only (the two with silent or injected
            # failure modes). DOM dump + full-page screenshot.
            if "jobs.lever.co" in url or "greenhouse.io" in url:
                dump_pre_submit_state(page)
                _save_presubmit_screenshot(page, url)

            # ── Phase 8: click submit + post-submit CAPTCHA handling ────
            click_submit_and_handle_captcha(
                page,
                submit_selector=submit_selector,
                captcha_solver=captcha_solver,
                captcha_solver_api_key=captcha_solver_api_key,
                captcha_solver_timeout=captcha_solver_timeout,
            )

            # ── Phase 9: early success check ────────────────────────────
            # Skip OTP path if we're already on a confirmation page
            # (Lever's confirmation says "A confirmation email has been
            # sent" which would otherwise look like an OTP prompt).
            early_success, _ = detect_submit_success(
                page, success_url_fragments, submit_selector=submit_selector,
            )
            if early_success:
                log.info("early success detected before OTP check")

            # ── Phase 10: email OTP verification (Greenhouse) ───────────
            if not early_success and detect_email_verification(page):
                handle_email_verification(
                    page,
                    imap_server=imap_server,
                    imap_port=imap_port,
                    imap_email=imap_email,
                    imap_password=imap_password,
                    imap_code_timeout=imap_code_timeout,
                )

            # ── Phase 11: final success detect + outcome ────────────────
            success, error_str = check_submit_success(
                page,
                url=url,
                success_url_fragments=success_url_fragments,
                submit_selector=submit_selector,
            )

            return {
                "ok": success,
                "url": page.url,
                "error": error_str,
                "field_errors": field_errors,
            }

        finally:
            ctx.close()
            browser.close()


def _save_presubmit_screenshot(page: Any, url: str) -> None:
    """Save a full-page screenshot, tagged by URL hash for de-dup.

    Lets us visually verify React-Select state (the ``singleValue`` text
    that's the source of truth for "what did we actually pick") without
    reverse-engineering class names. Failure is non-fatal — just DEBUG-log.
    """
    try:
        shot_dir = Path("state") / "failed_submits"
        shot_dir.mkdir(parents=True, exist_ok=True)
        tag = hashlib.md5(url.encode()).hexdigest()[:10]
        path = shot_dir / f"presubmit_{tag}.png"
        page.screenshot(path=str(path), full_page=True)
        log.info("pre-submit screenshot: %s", path)
    except Exception as exc:
        log.debug("pre-submit screenshot failed: %s", exc)
