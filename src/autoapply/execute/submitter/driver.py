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
    stop_before_submit: bool = False,
    keep_open_seconds: int = 1800,
) -> dict[str, Any]:
    """Drive a single Playwright-backed form submit.

    Returns ``{"ok": bool, "url": str, "error": str|None, "field_errors": list[str]}``.

    Raises :class:`CaptchaDetected` if a blocking captcha wall can't be
    solved (or no solver is configured) — caller routes the app to review.
    Raises :class:`SubmitFailed` on unrecoverable browser / navigation /
    OTP-retrieval errors.

    :param stop_before_submit: When ``True``, run every fill phase
        (1-7) and then BLOCK on the page being closed instead of
        clicking submit. This is the "open prefilled in a real
        browser" path used by the local web UI's retry-on-failure
        button — captcha walls / spam-flag blocks / review-required
        applications can be re-opened with every field already
        filled and the user just clicks Submit themselves.
        Implies ``headless=False`` (callers should set both).
    :param keep_open_seconds: Maximum time to leave the window open
        when ``stop_before_submit=True``. Default 30 minutes.
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
            # Post-navigate settle — lets the SPA's captcha / analytics
            # scripts initialize before we start poking the form.
            # Ashby especially: its invisible reCAPTCHA widget only
            # starts generating tokens after this initialization
            # completes.
            _is_ashby = "ashbyhq.com" in url
            jitter(2.0, 3.5) if _is_ashby else jitter(1.0, 2.5)

            # Abort if CAPTCHA is immediately visible on the form page.
            if detect_captcha(page):
                raise CaptchaDetected(f"CAPTCHA on form page: {url}")

            # ── Phase 1.5: iframe-wrapper detection ─────────────────────
            # Many Greenhouse customers route applications through a
            # custom domain (e.g. Lyft on app.careerpuck.com) that
            # iframes the standard Greenhouse `/embed/job_app` form.
            # Our submitter's locators operate on the top-level
            # document only and would find nothing. Detect the embed
            # frame and re-navigate the top-level browser to its URL
            # — Greenhouse's embed pages work standalone.
            from .phases.iframe_resolve import resolve_form_iframe
            embed_url = resolve_form_iframe(page)
            if embed_url and embed_url != url:
                log.info(
                    "submit_form: detected iframe-wrapped form, "
                    "redirecting from %s to %s",
                    url, embed_url,
                )
                page.goto(embed_url, wait_until="domcontentloaded", timeout=60_000)
                jitter(2.0, 3.0)
                url = embed_url
                if detect_captcha(page):
                    raise CaptchaDetected(
                        f"CAPTCHA on embedded form page: {url}"
                    )

            # ── Phase 2: upload files + wait for Lever resume analysis ──
            # Must precede fill; Lever's re-render clears text on analysis.
            upload_files(page, files, field_errors)
            if files:
                wait_for_resume_analysis(page)
                # Post-upload settle — Ashby's "Autofill from resume"
                # parses the PDF server-side and populates name/email/
                # phone/LinkedIn automatically. Give it room to finish.
                if _is_ashby:
                    jitter(2.5, 4.0)

            # ── Phase 3: fill API-sourced fields ────────────────────────
            fill_api_fields(page, data, field_errors)
            if _is_ashby:
                # Let Ashby's change-event handlers settle and any
                # reCAPTCHA interaction-based scoring update.
                jitter(1.0, 2.0)

            # ── Phase 4: Lever-only qualifying-card questions ───────────
            # Lever qualifying questions appear in the DOM as
            # input[name="cards[UUID][fieldN]"] but are NOT returned by
            # the public posting API — we discover them at runtime.
            if "jobs.lever.co" in url:
                fill_lever_cards(page, set(data.keys()), field_errors)

            # ── Phase 4.5: expand repeating-group sections ───────────────
            # Greenhouse embed forms (Lyft via careerpuck, etc.) render
            # Employment + Education with only ONE row visible — the
            # candidate has to click "Add another" to expose
            # company-name-1, title-1, school--1, etc. Stage-2 only
            # scrapes fields currently in the DOM, so without expanding
            # upfront we'd only fill the first experience and the first
            # education entry. Click "Add another" enough times to
            # match the candidate's resume (capped at 5).
            try:
                from .phases.expand_groups import expand_repeating_groups
                profile_obj = (llm_context or {}).get("profile")
                expand_repeating_groups(page, profile_obj)
            except Exception as exc:
                log.debug("expand_groups phase raised: %s", exc)

            # ── Phase 4.6: deterministic Employment / Education fill ────
            # Fill repeating-group rows directly from profile.experiences
            # / profile.education. These fields have a 100% deterministic
            # mapping (company-name-0 IS experiences[0].company), so
            # sending them to Stage-2 LLM is wasted tokens AND risks
            # tipping the batch over a response-size limit (Lyft regression:
            # 4 employment rows × 6 fields = 24 extra fields → batch
            # failed wholesale → row 0 went from "Sociable AI" to empty).
            # Pre-filling here means Stage-2 only sees what's left.
            try:
                from .phases.repeating_groups_fill import (
                    fill_repeating_groups,
                )
                profile_obj = (llm_context or {}).get("profile")
                fill_repeating_groups(page, profile_obj)
            except Exception as exc:
                log.debug("repeating_groups_fill phase raised: %s", exc)

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

            # ── Phase 6.5: EEO radio groups (Ashby DOM-only path) ───────
            # Greenhouse + Lever expose EEO via their form-spec APIs —
            # those go through fill_api_fields. Ashby's hosted SPA does
            # NOT, so we walk radio groups, classify each group's
            # heading, and pick the option matching the profile EEO
            # value (or a decline-style option per
            # state/rules/eeo_semantics.yml).
            if "ashbyhq.com" in url:
                from .phases.eeo_radios import fill_eeo_radios
                profile_obj = (llm_context or {}).get("profile")
                fill_eeo_radios(page, profile_obj, field_errors)

            # ── Phase 6.7: auto-check consent / agreement checkboxes ────
            # Almost every ATS form has 1-2 unlabeled consent boxes
            # ("I agree to the privacy policy", "By submitting...")
            # that are de-facto compulsory but not marked ``required``.
            # The scrape pipeline misses them; this phase walks them
            # and clicks any whose nearby text matches a consent
            # keyword. Marketing opt-ins ("Subscribe to alerts...")
            # are explicitly skipped via a negative keyword list.
            from .phases.auto_consent import auto_check_consent_boxes
            auto_check_consent_boxes(page, field_errors)

            # ── Phase 7: pre-submit diagnostic dump ─────────────────────
            # Lever + Greenhouse + Ashby. DOM dump + full-page screenshot.
            # Need this on Ashby especially because the hosted SPA
            # obscures which DOM attributes get used for each field,
            # and without the dump there's no visibility into why
            # the submit button wasn't clickable.
            if (
                "jobs.lever.co" in url
                or "greenhouse.io" in url
                or "ashbyhq.com" in url
            ):
                dump_pre_submit_state(page)
                _save_presubmit_screenshot(page, url)

            # ── Optional: prefill-only mode ─────────────────────────────
            # Used by the local web UI's "Open prefilled" button on
            # failed applications. Every field has been filled by the
            # phases above; we now hand the browser back to the user
            # so they can solve the captcha / OTP / unfilled-field
            # themselves and click Submit. We keep the browser open by
            # blocking on `page.wait_for_event("close", ...)` until
            # the user closes the window or the timeout expires.
            if stop_before_submit:
                log.info(
                    "submit_form: stop_before_submit=True — leaving "
                    "browser open for up to %ds; close the window to "
                    "release.",
                    keep_open_seconds,
                )
                try:
                    page.wait_for_event(
                        "close", timeout=keep_open_seconds * 1000,
                    )
                except Exception as exc:
                    log.info(
                        "prefill window timed out / errored: %s "
                        "(this is expected if the user took too long)",
                        exc,
                    )
                return {
                    "ok": True,
                    "url": page.url if not page.is_closed() else url,
                    "error": None,
                    "field_errors": field_errors,
                    "prefilled": True,
                }

            # ── Phase 7.5: pre-submit captcha token check/solve ──────────
            # Ashby runs invisible reCAPTCHA v3 / Turnstile — the
            # response token is supposed to populate automatically
            # after user interactions, but headless browsers often
            # get a missing token that the backend flags as spam.
            # Wait briefly for auto-population, and if still empty +
            # a solver is configured, solve + inject the token before
            # clicking submit. No-op when no captcha detected.
            from .phases.pre_submit import ensure_captcha_token
            ensure_captcha_token(
                page,
                solver=captcha_solver,
                api_key=captcha_solver_api_key,
                timeout=captcha_solver_timeout,
                settle_seconds=3.0 if "ashbyhq.com" in url else 1.0,
            )

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
