"""The orchestrator — one function that drives a single form submission.

:func:`submit_form` owns the whole sequence:

    launch browser → navigate → pre-nav cookies → stealth →
    upload files → wait for Lever resume analysis → fill text fields →
    fill Lever cards → pre-submit diagnostic dump → click submit →
    post-submit captcha check → solver retry → email OTP verification →
    success detector → post-submit diagnostic dump → return outcome dict

Every subroutine lives in a sibling module — this module just composes them.
"""

from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import Any

from autoapply.rules import load_rules

from . import CaptchaDetected, SubmitFailed
from .captcha_detect import wait_for_captcha, detect_captcha
from .captcha_retry import maybe_solve_and_retry_captcha
from .diagnostics import dump_pre_submit_state, dump_post_submit_failure
from .dom_batch import batch_resolve_dom_fields
from .field_fill import fill_field
from .file_upload import file_input_selector, upload_file
from .label_fallback import fill_by_label
from .imap_otp import (
    detect_email_verification,
    enter_verification_code,
    fetch_imap_verification_code,
)
from .lever_cards import fill_lever_cards
from .success_detect import detect_submit_success
from .util import inner_text_safe, jitter


log = logging.getLogger(__name__)


# UA pool loaded from state/rules/browser_pool.yml. ``tuple(...)`` converts
# the YAML list to the immutable shape the random.choice sampler expects.
USER_AGENTS: tuple[str, ...] = tuple(load_rules("browser_pool")["user_agents"])


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
    from playwright.sync_api import TimeoutError as PWTimeout  # noqa: F401

    field_errors: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1280, "height": 900},
            locale="en-US",
            timezone_id="America/New_York",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )

        # Inject pre-navigation cookies (e.g. hCaptcha accessibility bypass).
        if pre_navigation_cookies:
            ctx.add_cookies(pre_navigation_cookies)

        page = ctx.new_page()

        # playwright-stealth — hide webdriver fingerprint.
        # Supports both v1 (stealth_sync) and v2 (Stealth().use_sync) APIs.
        try:
            from playwright_stealth import Stealth  # type: ignore[import]
            Stealth().use_sync(page)
        except ImportError:
            try:
                from playwright_stealth import stealth_sync  # type: ignore[import]
                stealth_sync(page)
            except Exception:
                log.debug("playwright-stealth unavailable — continuing without")
        except Exception:
            log.debug("playwright-stealth failed — continuing without")

        try:
            log.info("playwright: navigating to %s", url)
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            jitter(1.0, 2.5)

            # Abort if CAPTCHA is immediately visible.
            if detect_captcha(page):
                raise CaptchaDetected(f"CAPTCHA on form page: {url}")

            # ------ Upload files FIRST (resume, cover_letter) ---------------
            # Lever's React upload widget parses the PDF asynchronously and then
            # re-renders the form to pre-populate fields (name, email, phone).
            # If we fill text fields first, Lever's re-render after resume
            # analysis clears them.  Upload → wait for analysis → fill text.
            _upload_all(page, files, field_errors)

            # Wait for async resume analysis (Lever does this in the background).
            # The widget shows "Analyzing resume…" while the PDF is being parsed
            # server-side; we must wait for "success!" before filling text fields
            # so Lever's post-analysis re-render doesn't wipe our fills.
            if files:
                try:
                    page.wait_for_function(
                        "() => !document.body.innerText.toLowerCase().includes('analyzing resume')",
                        timeout=20_000,
                    )
                    log.debug("resume analysis complete")
                    jitter(0.5, 1.0)
                except Exception:
                    log.debug("timed out waiting for resume analysis; proceeding anyway")

            # ------ Fill text / select / textarea fields (AFTER upload) ------
            # ``submit_greenhouse`` pre-augments `data` with best-guess
            # values for SPA-injected fields (country, location, city,
            # state, zip, postal_code) that aren't in Greenhouse's API
            # questions list. If the tenant's DOM doesn't have those
            # specific input names, ``fill_field`` raises ValueError
            # "no element found" — which is informational, not a real
            # fill error. We swallow those so they don't pollute
            # ``field_errors``.
            for name, value in data.items():
                if not value:
                    continue
                try:
                    fill_field(page, name, str(value))
                    jitter(0.1, 0.4)
                except ValueError as exc:
                    # "no element found for name/id=..." — field absent
                    # on this tenant's DOM. Log at DEBUG only.
                    msg = str(exc)
                    if "no element found" in msg:
                        log.debug("fill skipped (field absent): %r", name)
                    else:
                        log.debug("fill error field=%r: %s", name, exc)
                        field_errors.append(f"fill:{name}:ValueError")
                except Exception as exc:
                    log.debug("fill error field=%r: %s", name, exc)
                    field_errors.append(f"fill:{name}:{type(exc).__name__}")

            # ------ Fill Lever card (qualifying) questions dynamically -------
            # Lever qualifying questions appear in the DOM as
            # input[name="cards[UUID][fieldN]"] / select[name="cards[UUID][fieldN]"]
            # but are NOT returned by the public posting API. We discover them
            # at Playwright time and fill using question-text heuristics.
            if "jobs.lever.co" in url:
                fill_lever_cards(page, set(data.keys()), field_errors)

            # ------ Stage-2 DOM batch LLM resolver --------------------------
            # The new job-boards.greenhouse.io SPA injects required fields
            # that aren't in the API ``/questions`` response (School on
            # jjsnackfoods, Gender on Axon/Smartsheet, ...). Stage-1
            # answered everything the API exposed; Stage-2 now scrapes
            # the post-upload DOM for remaining empty required fields,
            # opens their dropdowns to capture options, and resolves the
            # whole batch in ONE Gemini call.
            #
            # Runs BEFORE ``label_fallback`` so the LLM gets first crack
            # at SPA-injected fields — the deterministic label_fallback
            # remains as a safety net for the small set of fields we
            # have hardcoded answers for (location atoms primarily).
            stage2_audit: dict[str, Any] = {}
            if llm_context:
                try:
                    stage2_audit = batch_resolve_dom_fields(
                        page=page,
                        profile=llm_context.get("profile"),
                        answer_bank_yaml=llm_context.get("answer_bank_yaml", ""),
                        track=llm_context.get("track", "swe"),
                        company=llm_context.get("company", ""),
                        job_title=llm_context.get("job_title", ""),
                        already_filled_keys=set(data.keys()),
                    )
                    if stage2_audit.get("scraped_count"):
                        log.info(
                            "dom-batch: scraped=%d filled=%d model=%s error=%s",
                            stage2_audit.get("scraped_count", 0),
                            stage2_audit.get("filled_count", 0),
                            stage2_audit.get("model_used") or "<none>",
                            stage2_audit.get("error") or "—",
                        )
                        # Per-field audit — shows what the LLM picked
                        # for each DOM-injected field and whether the
                        # physical fill landed. Critical for diagnosing
                        # "LLM answered but form still rejected" cases.
                        for entry in stage2_audit.get("per_field", []):
                            mark = "✓" if entry.get("filled") else "✗"
                            log.info(
                                "  %s [%s] %s = %r  (conf=%.2f, %s)",
                                mark,
                                entry.get("source", "?"),
                                (entry.get("label") or entry.get("field_id", ""))[:50],
                                str(entry.get("value") or "")[:60],
                                entry.get("confidence", 0.0),
                                entry.get("reasoning", "")[:60],
                            )
                except Exception as exc:
                    log.warning("dom_batch stage-2 raised: %s", exc)

            # ------ Label-aware fallback (Greenhouse SPA-injected fields) ----
            # The new job-boards.greenhouse.io SPA renders tenant-specific
            # inputs that aren't in the API ``questions`` list. Stage-2
            # above handles these dynamically via LLM; this label_fallback
            # remains as a safety-net deterministic path for the small
            # set of location-atom fields where we have hardcoded answers
            # (city/state/zip) — useful when the LLM batch is rate-limited
            # or returns needs_review.
            if label_values:
                fill_by_label(page, label_values, set(data.keys()), field_errors)

            # ------ Pre-submit diagnostic dump (Lever + Greenhouse) --------
            # Lever was the original debugging target (silent server-side
            # rejections). Greenhouse added because the new
            # job-boards.greenhouse.io SPA injects tenant-specific fields
            # not in the API, and a DOM-state dump is the only way to
            # see them ahead of a fix.
            if "jobs.lever.co" in url or "greenhouse.io" in url:
                dump_pre_submit_state(page)
                # Also save a screenshot — lets us visually verify React-Select
                # state (singleValue text) without reverse-engineering class
                # names. Tag by URL hash to de-dup across tenants.
                try:
                    import hashlib as _hashlib
                    from pathlib import Path as _Path
                    shot_dir = _Path("state") / "failed_submits"
                    shot_dir.mkdir(parents=True, exist_ok=True)
                    tag = _hashlib.md5(url.encode()).hexdigest()[:10]
                    path = shot_dir / f"presubmit_{tag}.png"
                    page.screenshot(path=str(path), full_page=True)
                    log.info("pre-submit screenshot: %s", path)
                except Exception as exc:
                    log.debug("pre-submit screenshot failed: %s", exc)

            # ------ Submit -------------------------------------------------
            jitter(1.0, 3.0)
            try:
                page.click(submit_selector, timeout=10_000)
            except Exception as exc:
                raise SubmitFailed(
                    f"Submit button not clickable at {url}: {exc}"
                ) from exc

            # Wait for post-submit page load.
            try:
                page.wait_for_load_state("networkidle", timeout=15_000)
            except Exception:
                pass  # SPA may not trigger networkidle; check content below.
            jitter(1.0, 2.0)

            # CAPTCHA on the post-submit page?
            # hCaptcha's challenge modal can take several seconds to render
            # after the submit click (async challenge fetch + image preload),
            # so poll a short window instead of one-shot checking.
            if wait_for_captcha(page, timeout=12.0):
                # If a third-party solver is configured, try to solve the
                # hCaptcha and retry the submit once. Otherwise surface as
                # CaptchaDetected so the caller routes the app to review.
                solved = maybe_solve_and_retry_captcha(
                    page,
                    submit_selector=submit_selector,
                    solver=captcha_solver,
                    api_key=captcha_solver_api_key,
                    timeout=captcha_solver_timeout,
                )
                if not solved:
                    raise CaptchaDetected("CAPTCHA appeared after submit attempt")

            # Early success check — if the post-submit page is already a
            # confirmation page, skip the OTP path (Lever's confirmation page
            # says "A confirmation email has been sent" which would otherwise
            # be mistaken for an OTP prompt).
            _already_success, _early_reason = detect_submit_success(
                page,
                success_url_fragments,
                submit_selector=submit_selector,
            )
            if _already_success:
                log.info("early success detected: %s", _early_reason)

            # Email verification step? (Greenhouse sends a one-time code.)
            # Only enter this path when:
            #   (a) we are NOT already on a success page, AND
            #   (b) there is an actual code INPUT field on the page
            #       (not just confirmation-email text in the body copy).
            if not _already_success and detect_email_verification(page):
                _handle_email_verification(
                    page,
                    imap_server=imap_server,
                    imap_port=imap_port,
                    imap_email=imap_email,
                    imap_password=imap_password,
                    imap_code_timeout=imap_code_timeout,
                )

            # ------ Detect success ----------------------------------------
            success, reason = detect_submit_success(
                page,
                success_url_fragments,
                submit_selector=submit_selector,
            )

            error_str: str | None = None
            if success:
                log.info("submit success (%s): %s", reason, page.url)
            else:
                error_str = reason
                log.warning("submit appears unsuccessful for %s: %s", url, reason)
                # Log page text for diagnosis — helps tune success signals.
                try:
                    page_text = inner_text_safe(page).lower()
                    log.info(
                        "post-submit page text (first 600 chars): %s",
                        page_text[:600],
                    )
                except Exception:
                    pass

                # Lever-only: save a screenshot + mark any hCaptcha iframes we
                # can see. Helps debug silent hCaptcha fails without standing
                # up a headful browser.
                if "jobs.lever.co" in url:
                    dump_post_submit_failure(page, url)

            return {
                "ok": success,
                "url": page.url,
                "error": error_str,
                "field_errors": field_errors,
            }

        finally:
            ctx.close()
            browser.close()


# ── Driver-internal helpers (not exposed as public utilities) ─────────────


def _upload_all(
    page: Any, files: dict[str, str], field_errors: list[str]
) -> None:
    """Iterate ``files`` and upload each via :func:`file_upload.upload_file`.

    Handles the cover-letter-as-text case by writing to a temp .txt file
    before upload, and cleans up those temp files before returning.
    """
    tmp_files_to_delete: list[str] = []
    for name, path in files.items():
        if not path:
            continue

        # cover_letter may arrive as raw text (not a file path) when the
        # generator produced text content rather than a PDF file.
        # Write to a temp .txt file so Playwright can upload it.
        abs_path = Path(path)
        if not abs_path.exists():
            if name == "cover_letter" and len(path) > 20:
                import tempfile
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

    # Clean up any temp files we created.
    for tmp_path in tmp_files_to_delete:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass


def _handle_email_verification(
    page: Any,
    *,
    imap_server: str,
    imap_port: int,
    imap_email: str,
    imap_password: str,
    imap_code_timeout: int,
) -> None:
    """Fetch the OTP code via IMAP and type it in. Raises ``SubmitFailed``
    if credentials are missing or the code doesn't arrive in time."""
    log.info("email verification required; fetching code via IMAP …")
    if not (imap_email and imap_password):
        raise SubmitFailed(
            "email verification required but IMAP credentials "
            "not configured (set IMAP_EMAIL + IMAP_PASSWORD)"
        )

    code = fetch_imap_verification_code(
        imap_server=imap_server,
        imap_port=imap_port,
        imap_email=imap_email,
        imap_password=imap_password,
        timeout=imap_code_timeout,
    )
    if not code:
        raise SubmitFailed(
            "email verification required but code not found "
            f"in inbox within {imap_code_timeout}s"
        )

    log.info("verification code fetched: %s", code)
    enter_verification_code(page, code)

    # Wait for result after code entry.
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:
        pass
    jitter(1.5, 3.0)
