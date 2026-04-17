"""Playwright-based form fill + submit for Greenhouse and Lever applications.

Called by the applicator's submit() method only when DRY_RUN=False.
Uses playwright-stealth to minimise bot-detection fingerprinting.

Outcome dict:
    {"ok": bool, "url": str, "error": str | None, "field_errors": list[str]}

Raises:
    CaptchaDetected — a CAPTCHA wall was hit; route app to review queue.
    SubmitFailed    — unrecoverable browser / network error.
"""

from __future__ import annotations

import logging
import random
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# ---- Page-state heuristics ------------------------------------------------

_CAPTCHA_SIGNALS: tuple[str, ...] = (
    "recaptcha",
    "hcaptcha",
    "cf-challenge",
    "g-recaptcha",
    "please verify you are human",
    "complete the security check",
    "captcha",
    "cloudflare ray id",
)

_SUCCESS_SIGNALS: tuple[str, ...] = (
    "thank you for applying",
    "application submitted",
    "application received",
    "we've received your application",
    "your application has been submitted",
    "successfully submitted",
    "thanks for applying",
    "we have received your application",
    "application complete",
    "your application is complete",
)

_USER_AGENTS: tuple[str, ...] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
)


# ---- Public exceptions ----------------------------------------------------


class CaptchaDetected(Exception):
    """A CAPTCHA wall was encountered; route application to the review queue."""


class SubmitFailed(Exception):
    """Unrecoverable error during browser navigation, form fill, or submit."""


# ---- Public entry points ---------------------------------------------------


def submit_greenhouse(
    *,
    board_token: str,
    job_id: str,
    data: dict[str, Any],
    files: dict[str, str],
    headless: bool = True,
) -> dict[str, Any]:
    """Fill and submit a Greenhouse application form via Playwright.

    Returns an outcome dict: {"ok": bool, "url": str, "error": str|None,
    "field_errors": list[str]}.
    Raises CaptchaDetected if a CAPTCHA wall is detected at any point.
    """
    url = f"https://boards.greenhouse.io/{board_token}/jobs/{job_id}"
    return _submit_form(
        url=url,
        data=data,
        files=files,
        headless=headless,
        # Greenhouse uses a <button id="submit_app"> plus the generic submit
        # button selector as a fallback.
        submit_selector=(
            "#submit_app, "
            'button[type="submit"], '
            'input[type="submit"]'
        ),
        success_url_fragments=("confirmation", "thank", "success", "submitted"),
    )


def submit_lever(
    *,
    token: str,
    posting_id: str,
    data: dict[str, Any],
    files: dict[str, str],
    headless: bool = True,
) -> dict[str, Any]:
    """Fill and submit a Lever application form via Playwright."""
    url = f"https://jobs.lever.co/{token}/{posting_id}/apply"
    return _submit_form(
        url=url,
        data=data,
        files=files,
        headless=headless,
        submit_selector='button[type="submit"], input[type="submit"]',
        success_url_fragments=("confirmation", "thank", "success", "submitted"),
    )


# ---- Internal form driver --------------------------------------------------


def _submit_form(
    *,
    url: str,
    data: dict[str, Any],
    files: dict[str, str],
    headless: bool,
    submit_selector: str,
    success_url_fragments: tuple[str, ...],
) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright
    from playwright.sync_api import TimeoutError as PWTimeout  # noqa: F401

    field_errors: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(
            user_agent=random.choice(_USER_AGENTS),
            viewport={"width": 1280, "height": 900},
            locale="en-US",
            timezone_id="America/New_York",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )

        page = ctx.new_page()

        # playwright-stealth — hide webdriver fingerprint.
        try:
            from playwright_stealth import stealth_sync  # type: ignore[import]
            stealth_sync(page)
        except Exception:
            log.debug("playwright-stealth unavailable or failed — continuing without")

        try:
            log.info("playwright: navigating to %s", url)
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            _jitter(1.0, 2.5)

            # Abort if CAPTCHA is immediately visible.
            if _detect_captcha(page):
                raise CaptchaDetected(f"CAPTCHA on form page: {url}")

            # ------ Fill text / select / textarea fields -------------------
            for name, value in data.items():
                if not value:
                    continue
                try:
                    _fill_field(page, name, str(value))
                    _jitter(0.1, 0.4)
                except Exception as exc:
                    log.debug("fill error field=%r: %s", name, exc)
                    field_errors.append(f"fill:{name}:{type(exc).__name__}")

            # ------ Upload files (resume, cover_letter) --------------------
            for name, path in files.items():
                if not path:
                    continue
                abs_path = Path(path)
                if not abs_path.exists():
                    log.warning("file not found for upload: %s", abs_path)
                    field_errors.append(f"missing_file:{name}")
                    continue
                try:
                    page.set_input_files(
                        f'input[name="{name}"]', str(abs_path), timeout=8_000
                    )
                    log.debug("uploaded %s → %s", abs_path.name, name)
                    _jitter(0.5, 1.5)
                except Exception as exc:
                    log.debug("upload error field=%r: %s", name, exc)
                    field_errors.append(f"upload:{name}:{type(exc).__name__}")

            # ------ Submit -------------------------------------------------
            _jitter(1.0, 3.0)
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
            _jitter(1.0, 2.0)

            # CAPTCHA on the post-submit page?
            if _detect_captcha(page):
                raise CaptchaDetected("CAPTCHA appeared after submit attempt")

            # ------ Detect success ----------------------------------------
            final_url = page.url.lower()
            page_text = _inner_text_safe(page).lower()

            success = any(sig in page_text for sig in _SUCCESS_SIGNALS)
            if not success:
                success = any(frag in final_url for frag in success_url_fragments)

            error_str: str | None = None
            if not success:
                error_str = _collect_page_errors(page) or (
                    f"no success signal detected; final_url={page.url}"
                )
                log.warning("submit appears unsuccessful for %s: %s", url, error_str)

            return {
                "ok": success,
                "url": page.url,
                "error": error_str,
                "field_errors": field_errors,
            }

        finally:
            ctx.close()
            browser.close()


# ---- Field fill helper -----------------------------------------------------


def _fill_field(page: Any, name: str, value: str) -> None:
    """Fill one form field identified by its `name` attribute.

    Strategy (in order):
    1. <select>          → select_option by label text, then value attribute.
    2. <input type=radio> → check radio whose label text or value matches.
    3. <input type=checkbox> → check/uncheck based on truthy value string.
    4. Everything else   → fill() (text, textarea, email, number …).
    """
    # 1. <select> -------------------------------------------------------
    sel_loc = page.locator(f'select[name="{name}"]')
    if sel_loc.count() > 0:
        _fill_select(sel_loc.first, value)
        return

    # 2. Radio buttons --------------------------------------------------
    radio_group = page.locator(f'input[type="radio"][name="{name}"]')
    if radio_group.count() > 0:
        _fill_radio(page, radio_group, name, value)
        return

    # 3. Checkbox -------------------------------------------------------
    cb_loc = page.locator(f'input[type="checkbox"][name="{name}"]')
    if cb_loc.count() > 0:
        truthy = value.strip().lower() in ("yes", "true", "1", "on")
        if truthy:
            cb_loc.first.check(timeout=3_000)
        else:
            cb_loc.first.uncheck(timeout=3_000)
        return

    # 4. Text / textarea / number / email / tel -------------------------
    loc = page.locator(f'[name="{name}"]').first
    loc.fill(value, timeout=5_000)


def _fill_select(sel_el: Any, value: str) -> None:
    """Try three strategies to pick the right <select> option."""
    v_lower = value.strip().lower()

    # Exact label match.
    try:
        sel_el.select_option(label=value, timeout=3_000)
        return
    except Exception:
        pass

    # Exact value= attribute match.
    try:
        sel_el.select_option(value=value, timeout=3_000)
        return
    except Exception:
        pass

    # Case-insensitive prefix match on option text.
    try:
        opts = sel_el.locator("option").all()
        for opt in opts:
            if opt.inner_text().strip().lower().startswith(v_lower):
                sel_el.select_option(label=opt.inner_text().strip(), timeout=3_000)
                return
    except Exception:
        pass


def _fill_radio(page: Any, radio_group: Any, name: str, value: str) -> None:
    """Check the radio button whose value= attribute or label text matches value."""
    v_lower = value.strip().lower()

    # By value= attribute (exact, then case-insensitive).
    exact_val = page.locator(f'input[type="radio"][name="{name}"][value="{value}"]')
    if exact_val.count() > 0:
        exact_val.first.check(timeout=3_000)
        return

    # Case-insensitive value= search.
    try:
        for radio in radio_group.all():
            rv = (radio.get_attribute("value") or "").strip().lower()
            if rv == v_lower:
                radio.check(timeout=3_000)
                return
    except Exception:
        pass

    # By label text (look for <label for="id">).
    try:
        for radio in radio_group.all():
            rid = radio.get_attribute("id") or ""
            if rid:
                lbl = page.locator(f'label[for="{rid}"]')
                if lbl.count() > 0:
                    label_text = lbl.first.inner_text().strip().lower()
                    if label_text == v_lower:
                        radio.check(timeout=3_000)
                        return
    except Exception:
        pass


# ---- Utility helpers -------------------------------------------------------


def _detect_captcha(page: Any) -> bool:
    try:
        content = page.content().lower()
        return any(sig in content for sig in _CAPTCHA_SIGNALS)
    except Exception:
        return False


def _inner_text_safe(page: Any) -> str:
    try:
        return page.inner_text("body") or ""
    except Exception:
        try:
            return page.content()
        except Exception:
            return ""


def _collect_page_errors(page: Any) -> str:
    """Scrape visible error messages from the current page."""
    selectors = [
        ".error",
        ".alert-error",
        ".alert-danger",
        '[class*="error"]',
        ".invalid-feedback",
        '[data-error]',
        ".field-error",
        ".form-error",
    ]
    msgs: list[str] = []
    for sel in selectors:
        try:
            for loc in page.locator(sel).all()[:3]:
                txt = loc.inner_text().strip()
                if txt:
                    msgs.append(txt[:120])
        except Exception:
            pass
    return "; ".join(msgs[:5])


def _jitter(low: float, high: float) -> None:
    """Sleep for a random duration in [low, high] seconds."""
    time.sleep(random.uniform(low, high))
