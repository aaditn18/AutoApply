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

# NOTE: reCAPTCHA v3 (invisible) is embedded on almost every Greenhouse /
# Lever form as a background risk-score signal — it never shows a user
# challenge. We must NOT flag it. Only flag actual blocking challenges.
#
# Signals that indicate an interactive / blocking CAPTCHA:
#   - Cloudflare interstitial: cf-challenge-running JS var, "Just a moment"
#   - reCAPTCHA v2 interactive: api2/anchor or api2/bframe iframe appear
#   - hCaptcha challenge iframe
#   - Explicit "verify you are human" overlay text

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
    imap_server: str = "imap.gmail.com",
    imap_port: int = 993,
    imap_email: str = "",
    imap_password: str = "",
    imap_code_timeout: int = 90,
) -> dict[str, Any]:
    """Fill and submit a Greenhouse application form via Playwright.

    Returns an outcome dict: {"ok": bool, "url": str, "error": str|None,
    "field_errors": list[str]}.
    Raises CaptchaDetected if a CAPTCHA wall is detected at any point.

    If the Greenhouse board requires email verification (an OTP sent to the
    applicant's address), the code is fetched automatically via IMAP when
    imap_email + imap_password are provided.
    """
    url = f"https://boards.greenhouse.io/{board_token}/jobs/{job_id}"

    # The new job-boards.greenhouse.io SPA always adds a `country` combobox
    # (React-Select) that is NOT part of the API questions list.  Inject it
    # here so _fill_field sees it and fills it before submit.
    augmented_data = {"country": "United States", **data}

    return _submit_form(
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
) -> dict[str, Any]:
    """Fill and submit a Lever application form via Playwright.

    hcaptcha_accessibility_token — if provided (register once at
    https://accounts.hcaptcha.com/accessibility), it is injected as the
    `hc_accessibility` cookie on the hcaptcha.com domain before navigating
    to the apply page.  This causes hCaptcha to issue a silent pass token
    without showing a challenge to the headless browser.

    imap_* — credentials for fetching the email verification code that some
    Lever boards send after the initial form submit (same OTP flow as
    Greenhouse).
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

    return _submit_form(
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
    pre_navigation_cookies: list[dict[str, Any]] | None = None,
    imap_server: str = "imap.gmail.com",
    imap_port: int = 993,
    imap_email: str = "",
    imap_password: str = "",
    imap_code_timeout: int = 90,
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
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            _jitter(1.0, 2.5)

            # Abort if CAPTCHA is immediately visible.
            if _detect_captcha(page):
                raise CaptchaDetected(f"CAPTCHA on form page: {url}")

            # ------ Upload files FIRST (resume, cover_letter) ---------------
            # Lever's React upload widget parses the PDF asynchronously and then
            # re-renders the form to pre-populate fields (name, email, phone).
            # If we fill text fields first, Lever's re-render after resume
            # analysis clears them.  Upload → wait for analysis → fill text.
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
                selector = _file_input_selector(page, name)
                if not selector:
                    log.debug("no file input found for field=%r; skipping", name)
                    field_errors.append(f"no_input:{name}")
                    continue
                try:
                    _upload_file(page, selector, str(abs_path))
                    log.debug("uploaded %s → %s (selector=%r)", abs_path.name, name, selector)
                    _jitter(0.5, 1.5)
                except Exception as exc:
                    log.debug("upload error field=%r: %s", name, exc)
                    field_errors.append(f"upload:{name}:{type(exc).__name__}")

            # Clean up any temp files we created.
            for tmp_path in tmp_files_to_delete:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except Exception:
                    pass

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
                    _jitter(0.5, 1.0)
                except Exception:
                    log.debug("timed out waiting for resume analysis; proceeding anyway")

            # ------ Fill text / select / textarea fields (AFTER upload) ------
            for name, value in data.items():
                if not value:
                    continue
                try:
                    _fill_field(page, name, str(value))
                    _jitter(0.1, 0.4)
                except Exception as exc:
                    log.debug("fill error field=%r: %s", name, exc)
                    field_errors.append(f"fill:{name}:{type(exc).__name__}")

            # ------ Fill Lever card (qualifying) questions dynamically -------
            # Lever qualifying questions appear in the DOM as
            # input[name="cards[UUID][fieldN]"] / select[name="cards[UUID][fieldN]"]
            # but are NOT returned by the public posting API. We discover them
            # at Playwright time and fill using question-text heuristics.
            if "jobs.lever.co" in url:
                _fill_lever_cards(page, set(data.keys()), field_errors)

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

            # Early success check — if we're already on a success/confirmation
            # URL or the page already shows a success signal, skip email
            # verification entirely and proceed to the outcome check below.
            # (Lever's confirmation page says "A confirmation email has been
            # sent" which would otherwise trigger the OTP path as a false
            # positive.)
            _early_url = page.url.lower()
            _early_text = _inner_text_safe(page).lower()
            _already_success = (
                any(frag in _early_url for frag in success_url_fragments)
                or any(sig in _early_text for sig in _SUCCESS_SIGNALS)
            )

            # Email verification step? (Greenhouse sends a one-time code.)
            # Only enter this path when:
            #   (a) we are NOT already on a success page, AND
            #   (b) there is an actual code INPUT field on the page
            #       (not just confirmation-email text in the body copy).
            if not _already_success and _detect_email_verification(page):
                log.info("email verification required; fetching code via IMAP …")
                if imap_email and imap_password:
                    code = _fetch_imap_verification_code(
                        imap_server=imap_server,
                        imap_port=imap_port,
                        imap_email=imap_email,
                        imap_password=imap_password,
                        timeout=imap_code_timeout,
                    )
                    if code:
                        log.info("verification code fetched: %s", code)
                        _enter_verification_code(page, code)
                        # Wait for result after code entry.
                        try:
                            page.wait_for_load_state("networkidle", timeout=15_000)
                        except Exception:
                            pass
                        _jitter(1.5, 3.0)
                    else:
                        raise SubmitFailed(
                            "email verification required but code not found "
                            f"in inbox within {imap_code_timeout}s"
                        )
                else:
                    raise SubmitFailed(
                        "email verification required but IMAP credentials "
                        "not configured (set IMAP_EMAIL + IMAP_PASSWORD)"
                    )

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
                # Log page text for diagnosis — helps tune success signals.
                log.warning("submit appears unsuccessful for %s: %s", url, error_str)
                log.info(
                    "post-submit page text (first 600 chars): %s",
                    page_text[:600],
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


# ---- Field fill helper -----------------------------------------------------


def _fill_field(page: Any, name: str, value: str) -> None:
    """Fill one form field identified by its `name` (or `id`) attribute.

    Strategy (in order):
    1. <select>          → select_option by label text, then value attribute.
    2. <input type=radio> → check radio whose label text or value matches.
    3. <input type=checkbox> → check/uncheck based on truthy value string.
    4. Everything else   → fill() (text, textarea, email, number …).

    All selectors try `[name="..."]` first, then fall back to `[id="..."]`
    because the new Greenhouse job-boards SPA omits `name` attributes and
    identifies fields by `id` only.
    """
    def _loc(attr_selector: str) -> Any:
        """Return the first matching locator using name= then id= fallback."""
        by_name = page.locator(f'[name="{name}"]{attr_selector}')
        if by_name.count() > 0:
            return by_name
        # id= fallback (new Greenhouse job-boards)
        by_id = page.locator(f'[id="{name}"]{attr_selector}')
        if by_id.count() > 0:
            return by_id
        # Some Greenhouse checkboxes append [] to the id: id="question_123[]"
        by_id_arr = page.locator(f'[id="{name}[]"]{attr_selector}')
        if by_id_arr.count() > 0:
            return by_id_arr
        return None

    # 1. <select> -------------------------------------------------------
    sel_loc = _loc("") or page.locator("__never__")
    # Narrow to actual <select> elements.
    actual_sel = page.locator(f'select[name="{name}"], select[id="{name}"]')
    if actual_sel.count() > 0:
        _fill_select(actual_sel.first, value)
        return

    # 2. Radio buttons --------------------------------------------------
    radio_name = page.locator(f'input[type="radio"][name="{name}"]')
    radio_id = page.locator(f'input[type="radio"][id*="{name}"]')
    radio_group = radio_name if radio_name.count() > 0 else radio_id
    if radio_group.count() > 0:
        _fill_radio(page, radio_group, name, value)
        return

    # 3. Checkbox -------------------------------------------------------
    cb_name = page.locator(f'input[type="checkbox"][name="{name}"]')
    cb_id = page.locator(f'input[type="checkbox"][id="{name}"]')
    cb_loc = cb_name if cb_name.count() > 0 else cb_id
    if cb_loc.count() > 0:
        truthy = value.strip().lower() in ("yes", "true", "1", "on")
        if truthy:
            cb_loc.first.check(timeout=3_000)
        else:
            cb_loc.first.uncheck(timeout=3_000)
        return

    # 4. Text / textarea / number / email / tel -------------------------
    # Try [name=], then [id=], then [id="name[]"], then case-insensitive name.
    for selector in (
        f'[name="{name}"]',
        f'[id="{name}"]',
        f'[id="{name}[]"]',
        # Case-insensitive CSS attribute selector (CSS4 "i" flag).
        # Handles Lever's camelCase URL fields: urls[LinkedIn] vs urls[linkedin].
        f'[name="{name}" i]',
    ):
        try:
            loc = page.locator(selector)
            if loc.count() > 0:
                el = loc.first
                # Detect React-Select / combobox pattern (role="combobox").
                # These need fill() + option click, not just fill().
                role = el.get_attribute("role") or ""
                if role == "combobox":
                    _fill_combobox(page, el, value)
                else:
                    el.fill(value, timeout=5_000)
                return
        except Exception:
            continue

    # Nothing found — will be recorded as a fill error by the caller.
    raise ValueError(f"no element found for name/id={name!r}")


def _fill_combobox(page: Any, input_el: Any, value: str) -> None:
    """Handle a React-Select / ARIA combobox: type the value then click the
    best matching option from the associated `[role='listbox']` dropdown.

    React-Select renders each combobox's option list inside a portal element
    at the document root.  The input's `aria-controls` attribute points to
    the specific listbox ID (e.g. `react-select-country-listbox`).  We scope
    the option search to that listbox to avoid accidentally clicking options
    belonging to a different combobox.

    Strategy:
    1. Click the input to open the dropdown.
    2. Clear + re-type the value (triggers client-side filtering).
    3. Wait briefly for options to populate.
    4. Click the option whose text best matches the value; fall back to
       pressing ArrowDown + Enter if no option text matches.
    """
    try:
        input_el.click(timeout=3_000)
        _jitter(0.1, 0.3)

        # Clear existing content then type the value so filtering fires.
        input_el.fill("", timeout=3_000)
        input_el.type(value, delay=40)
        _jitter(0.4, 0.8)  # let the dropdown filter

        # Scope to THIS combobox's listbox via aria-controls.
        # aria-controls is set dynamically after the combobox opens; read it
        # now (after the click + type above).
        # Use attribute selector [id="…"] instead of #id because some listbox
        # IDs contain CSS-special characters like `[]`.
        aria_controls = input_el.get_attribute("aria-controls") or ""
        if aria_controls:
            options = page.locator(f'[id="{aria_controls}"] [role="option"]')
        else:
            # Fallback: options inside a visible listbox only.
            options = page.locator("[role='listbox'] [role='option']")

        _jitter(0.1, 0.3)
        count = options.count()

        if count == 0:
            # Nothing in dropdown — press Enter to accept whatever is typed.
            input_el.press("Enter")
            return

        v_lower = value.strip().lower()

        # Prefer the option whose text exactly equals the target value.
        for i in range(min(count, len(value) + 30)):
            opt = options.nth(i)
            try:
                txt = opt.inner_text().strip().lower()
                if txt == v_lower:
                    opt.click(timeout=3_000)
                    return
            except Exception:
                continue

        # Exact match failed — try starts-with or contains.
        for i in range(min(count, 50)):
            opt = options.nth(i)
            try:
                txt = opt.inner_text().strip().lower()
                if txt.startswith(v_lower) or v_lower in txt:
                    opt.click(timeout=3_000)
                    return
            except Exception:
                continue

        # Still nothing — click the first visible option.
        options.first.click(timeout=3_000)

    except Exception as exc:
        log.debug("combobox fill failed for value=%r: %s", value, exc)
        raise


def _fill_select(sel_el: Any, value: str) -> None:
    """Try several strategies to pick the right <select> option."""
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

    try:
        opts = sel_el.locator("option").all()
        opt_texts = [o.inner_text().strip() for o in opts]
    except Exception:
        return

    # Case-insensitive prefix match on option text.
    for txt in opt_texts:
        if txt.lower().startswith(v_lower):
            try:
                sel_el.select_option(label=txt, timeout=3_000)
                return
            except Exception:
                pass

    # Substring match in either direction.
    for txt in opt_texts:
        tl = txt.lower()
        if v_lower in tl or tl in v_lower:
            try:
                sel_el.select_option(label=txt, timeout=3_000)
                return
            except Exception:
                pass

    # EEO "decline / prefer not" semantic fallback.
    # When our resolved value is a "decline to identify" variant but the actual
    # select uses different wording (e.g., Lever: "I do not want to answer"),
    # look for any option that conveys the same "no / decline" intent.
    _DECLINE_KEYWORDS = ("decline", "prefer not", "not wish", "not want",
                         "not identify", "not disclose", "choose not")
    _NO_KEYWORDS = ("no clearance", "none", "no polygraph", "not a protected")
    if any(kw in v_lower for kw in _DECLINE_KEYWORDS + _NO_KEYWORDS):
        for txt in opt_texts:
            tl = txt.lower()
            if any(kw in tl for kw in _DECLINE_KEYWORDS + _NO_KEYWORDS):
                try:
                    sel_el.select_option(label=txt, timeout=3_000)
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


def _upload_file(page: Any, selector: str, abs_path: str) -> None:
    """Upload a file to a file input, preferring the file-chooser API.

    The file-chooser API (expect_file_chooser → click → set_files) fires
    all browser-native events including the ones that React upload widgets
    (Lever, some Greenhouse tenants) listen to for state management.
    A plain set_input_files() call bypasses these handlers, which causes
    React to show spurious "file too large" or "invalid file" errors.

    Falls back to set_input_files() if the chooser dialog does not open
    within 3 s (e.g., the element is hidden / non-interactive).
    """
    try:
        with page.expect_file_chooser(timeout=3_000) as fc_info:
            # Click the input (or a label pointing to it) to open the chooser.
            try:
                page.locator(selector).click(timeout=3_000)
            except Exception:
                # If the input itself isn't clickable, look for an associated
                # <label> or a custom upload trigger button near it.
                pass
        fc = fc_info.value
        fc.set_files(abs_path)
        return
    except Exception:
        pass

    # Fallback: set_input_files directly (works for standard <input type=file>).
    page.set_input_files(selector, abs_path, timeout=8_000)


def _file_input_selector(page: Any, name: str) -> str | None:
    """Return a CSS selector that locates the file <input> for the given field name.

    Tries name= attribute first, then id= attribute (new Greenhouse job-boards
    SPA omits name attributes).  Returns None if nothing is found.
    """
    candidates = [
        f'input[type="file"][name="{name}"]',
        f'input[type="file"][id="{name}"]',
        f'input[type="file"]#{name}',
    ]
    for sel in candidates:
        try:
            if page.locator(sel).count() > 0:
                return sel
        except Exception:
            pass
    return None


def _detect_email_verification(page: Any) -> bool:
    """Return True if the current page is showing an OTP / code-entry step.

    Requires BOTH a phrase signal AND an actual code input field to be present.
    This prevents false positives from ATS confirmation pages that say things
    like "A confirmation email has been sent to you" (e.g. Lever's post-submit
    page) — those don't have a code input, so they won't match here.
    """
    # -- 1. Check for the Greenhouse-style multi-box OTP (security-input-*) ---
    try:
        if page.locator('input[id^="security-input-"]').count() > 0:
            return True
    except Exception:
        pass

    # -- 2. Check for a single-box OTP input with OTP-specific attributes -----
    try:
        otp_inputs = page.locator(
            'input[autocomplete="one-time-code"], '
            'input[inputmode="numeric"][maxlength], '
            'input[type="number"][maxlength]'
        )
        if otp_inputs.count() > 0:
            return True
    except Exception:
        pass

    # -- 3. Phrase + generic short-text input (belt-and-suspenders) -----------
    # Only fire when BOTH a phrase AND a short-text input are present, so that
    # ATS confirmation pages with "we sent you a confirmation email" don't
    # trigger this path without an actual input.
    try:
        content = page.content().lower()
    except Exception:
        return False

    _EMAIL_VERIFY_PHRASES = (
        "confirmation code",
        "verification code",
        "enter the code",
        "enter your code",
        "we sent a code",
        "paste the code",
        "code sent to",
        "security code",
    )
    has_phrase = any(phrase in content for phrase in _EMAIL_VERIFY_PHRASES)
    if not has_phrase:
        return False

    # Also require a visible text input with a short max-length.
    try:
        short_inputs = page.locator('input[type="text"][maxlength]')
        if short_inputs.count() > 0:
            return True
    except Exception:
        pass

    return False


def _react_set_value(page: Any, element_handle: Any, value: str) -> None:
    """Set a React-controlled input value and fire the synthetic events React needs.

    React uses a custom setter on HTMLInputElement.prototype so that synthetic
    onChange fires.  A plain `el.value = …` assignment bypasses that setter and
    leaves the button disabled.  We must call the original setter explicitly.
    """
    page.evaluate(
        """([el, val]) => {
            const setter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value').set;
            setter.call(el, val);
            el.dispatchEvent(new Event('input',  { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.dispatchEvent(new KeyboardEvent('keyup',
                { key: val.slice(-1) || '', bubbles: true }));
        }""",
        [element_handle, value],
    )


def _enter_verification_code(page: Any, code: str) -> None:
    """Type the verification code into the OTP input and click verify/submit.

    Handles two layouts:

    Layout A — Greenhouse new SPA: 8 individual single-character inputs with
      id="security-input-0" … id="security-input-7" (maxlength="1" each).
      Must fill one character per box and trigger React events per keystroke.

    Layout B — single combined input (older Greenhouse / other ATS):
      input[maxlength="6|8"], input[autocomplete="one-time-code"], etc.
      Fill whole code at once via React native-setter.

    After filling, wait for the submit button to enable (up to 8 s), then
    click it.  Force-click via JS as final fallback.
    """
    # ---- Layout A: multi-box OTP (Greenhouse new SPA) ----------------------
    # Detect by looking for at least one input whose id starts with
    # "security-input-".  The SPA renders security-input-0 … security-input-7.
    split_boxes = page.locator('input[id^="security-input-"]')
    box_count = split_boxes.count()
    if box_count > 0:
        log.debug(
            "detected %d-box OTP (security-input-* pattern); code=%r",
            box_count, code,
        )
        chars_to_fill = min(box_count, len(code))
        for i in range(chars_to_fill):
            # Prefer explicit id selector; fall back to nth in the group.
            box_loc = page.locator(f'[id="security-input-{i}"]')
            box = box_loc.first if box_loc.count() > 0 else split_boxes.nth(i)
            char = code[i]
            try:
                box.click(timeout=3_000)
                handle = box.element_handle()
                if handle:
                    _react_set_value(page, handle, char)
                else:
                    # Fallback: type the character (triggers keyboard events).
                    box.press("Backspace")
                    box.type(char, delay=60)
                _jitter(0.04, 0.12)
            except Exception as exc:
                log.debug("OTP box %d fill error (char=%r): %s", i, char, exc)

        log.debug("filled all %d OTP boxes", chars_to_fill)
        _jitter(0.3, 0.6)
        _click_otp_submit(page)
        return

    # ---- Layout B: single combined OTP input -------------------------------
    _OTP_SELECTORS = [
        'input[autocomplete="one-time-code"]',
        'input[type="text"][maxlength="8"]',
        'input[type="text"][maxlength="6"]',
        'input[type="number"][maxlength="6"]',
        'input[type="text"][maxlength="10"]',
        'input[type="text"][maxlength="5"]',
        'input[type="text"][maxlength="4"]',
        'input[placeholder*="code" i]',
        'input[name*="code" i]',
        'input[id*="code" i]',
    ]
    inp_el = None
    filled_sel = None
    for sel in _OTP_SELECTORS:
        loc = page.locator(sel)
        if loc.count() > 0:
            inp_el = loc.first
            filled_sel = sel
            break

    if inp_el is None:
        # Last resort: any visible, currently-empty text input.
        inputs = page.locator('input[type="text"]:visible')
        for i in range(inputs.count()):
            try:
                if inputs.nth(i).input_value() == "":
                    inp_el = inputs.nth(i)
                    filled_sel = "fallback:visible-empty"
                    break
            except Exception:
                continue

    if inp_el is None:
        log.warning("could not find OTP input field to enter verification code")
        return

    try:
        inp_el.click(timeout=3_000)
        inp_el.fill("", timeout=3_000)

        handle = inp_el.element_handle()
        if handle:
            _react_set_value(page, handle, code)
            log.debug("set OTP via React native-setter (sel=%r)", filled_sel)
        else:
            inp_el.type(code, delay=80)
            log.debug("set OTP via type() (sel=%r)", filled_sel)
    except Exception as exc:
        log.debug("OTP entry error: %s", exc)

    _jitter(0.4, 0.8)
    _click_otp_submit(page)


def _click_otp_submit(page: Any) -> None:
    """Wait for the verify/submit button to enable, then click it.

    After OTP entry the page's React logic enables the submit button
    asynchronously.  We poll for up to 8 s before falling back to a
    force-click via JS evaluate.
    """
    # Wait for ANY enabled submit button.
    try:
        page.wait_for_selector(
            "button[type='submit']:not([disabled]):not([aria-disabled='true'])",
            timeout=8_000,
        )
        log.debug("submit button enabled after OTP entry")
    except Exception:
        log.debug("submit button still disabled after 8s — will force-click")

    btn_selectors = [
        "button[type='submit']:not([disabled]):not([aria-disabled='true'])",
        'button:has-text("Submit application")',
        'button:has-text("Verify")',
        'button:has-text("Submit")',
        'button:has-text("Confirm")',
        'button:has-text("Continue")',
        'input[type="submit"]',
    ]
    for sel in btn_selectors:
        loc = page.locator(sel)
        if loc.count() > 0:
            try:
                loc.first.click(timeout=5_000)
                log.debug("clicked verify/submit button %r", sel)
                return
            except Exception:
                continue

    # Absolute last resort: remove disabled attr then JS-click.
    try:
        page.evaluate(
            """() => {
                const btn = document.querySelector('button[type="submit"]');
                if (btn) { btn.removeAttribute('disabled'); btn.click(); }
            }"""
        )
        log.debug("force-clicked submit button via JS (removed disabled attr)")
    except Exception as exc:
        log.warning("could not click verify button after entering OTP: %s", exc)


def _strip_html(html: str) -> str:
    """Strip HTML tags and decode entities, returning plain-text lines.

    Uses Python's built-in html.parser — no third-party dependency needed.
    Preserves newlines so the line-by-line code extractor works correctly.
    """
    import html as _html_mod
    import re as _re

    # Replace block-level tags with newlines so the structure is preserved.
    html = _re.sub(r"<(?:br|p|div|tr|td|li|h[1-6])[\s/>]", "\n", html, flags=_re.IGNORECASE)
    # Strip all remaining tags.
    html = _re.sub(r"<[^>]+>", " ", html)
    # Decode HTML entities (&amp; &nbsp; &#xNNN; etc.)
    html = _html_mod.unescape(html)
    # Collapse runs of whitespace/blank lines.
    lines = [ln.strip() for ln in html.splitlines()]
    return "\n".join(lines)


def _extract_code_from_plain_text(body: str) -> str | None:
    """Extract an OTP / security code from a plain-text email body.

    Strategy (line-by-line, no raw regex on concatenated HTML):
    1. Walk the lines; when we see a line that ends with ":" and contains
       a keyword like "code", "application", "paste", look at the next
       non-blank line.
    2. If that line is a standalone alphanumeric token (6–12 chars, no
       spaces, not a URL or common word), return it as the code.
    3. Fallback: scan every line for a short alphanumeric token that is
       entirely on its own line (no other text).
    """
    import re as _re

    _TRIGGER_RE = _re.compile(
        r"(?:code|application|paste|security|verification|confirm).*:\s*$",
        _re.IGNORECASE,
    )
    _CODE_LINE_RE = _re.compile(r"^[A-Za-z0-9]{6,12}$")

    lines = body.splitlines()

    # Pass 1: look for trigger line → next non-blank line = code.
    for i, line in enumerate(lines):
        stripped = line.strip()
        if _TRIGGER_RE.search(stripped):
            # Find the next non-blank line.
            for j in range(i + 1, min(i + 5, len(lines))):
                candidate = lines[j].strip()
                if candidate and _CODE_LINE_RE.match(candidate):
                    return candidate

    # Pass 2: any line that is ONLY a 6-12 char alphanumeric token.
    for line in lines:
        candidate = line.strip()
        if _CODE_LINE_RE.match(candidate):
            # Skip common false positives (pure lowercase English words,
            # the copyright year, etc.)
            if candidate.isdigit():
                continue  # bare numbers like "2026" → skip
            if candidate.islower() and len(candidate) <= 8:
                # Might be a common word; only accept if it contains digits
                if not any(c.isdigit() for c in candidate):
                    continue
            return candidate

    return None


def _fetch_imap_verification_code(
    *,
    imap_server: str,
    imap_port: int,
    imap_email: str,
    imap_password: str,
    timeout: int = 90,
) -> str | None:
    """Poll an IMAP inbox for a Greenhouse verification code email.

    Scans the INBOX for the most recent unread email from Greenhouse sent
    within the last `timeout` seconds and extracts the numeric/alphanumeric
    OTP from the message body.

    Returns the code string if found, or None on timeout / error.
    """
    import email as email_lib
    import imaplib
    import re
    from datetime import datetime, timezone, timedelta

    start_time = time.time()
    deadline = start_time + timeout
    poll_interval = 5  # seconds between IMAP polls
    # Only accept emails that arrived AFTER we started waiting (minus 30s slack).
    min_email_epoch = start_time - 30

    # Greenhouse format (confirmed from real emails):
    #   "Copy and paste this code into the security code field on your application:\n\nvjUsj6n6\nAfter..."
    # The code is 6-12 alphanumeric chars on its own line, immediately after a
    # line ending with ":".  We must NOT fall back to bare 4-digit numbers
    # because the email footer contains "© 2026 Greenhouse" which would match.
    _CODE_RE = re.compile(
        # Primary: line ending in ":" then blank line(s) then the code alone on a line.
        r":\s*[\r\n]+\s*([A-Za-z0-9]{6,12})\s*[\r\n]"
        r"|"
        # Secondary: "code is/:" immediately followed by the code (same line).
        r"(?:verification|confirmation|security)\s+code[^:\r\n]*:?\s*([A-Za-z0-9]{6,12})",
        re.IGNORECASE,
    )
    _SUBJECT_KEYWORDS = ("verify", "verification", "confirm", "code", "greenhouse")
    _SENDER_KEYWORDS = ("greenhouse", "no-reply", "noreply", "notification")

    while time.time() < deadline:
        try:
            mail = imaplib.IMAP4_SSL(imap_server, imap_port)
            mail.login(imap_email, imap_password)
            mail.select("INBOX")

            # Search all mail (read or unread) received today.
            # We filter by timestamp in Python — IMAP SINCE only has day
            # granularity and requiring UNSEEN breaks if the Gmail client
            # auto-marks incoming mail as read.
            # Use local time minus 1 day: IMAP SINCE compares against the
            # server's local clock, and UTC can be a day ahead of US time
            # zones — searching for "today UTC" would return 0 results in
            # the evening.  The epoch-based filter below handles freshness.
            since_str = (datetime.now() - timedelta(days=1)).strftime("%d-%b-%Y")
            _, msg_nums = mail.search(None, f"(SINCE {since_str})")

            ids = (msg_nums[0].split() if msg_nums and msg_nums[0] else [])
            # Process newest first.
            for num in reversed(ids):
                _, msg_data = mail.fetch(num, "(RFC822)")
                if not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                if not isinstance(raw, bytes):
                    continue
                msg = email_lib.message_from_bytes(raw)

                subject = str(msg.get("subject", "")).lower()
                sender = str(msg.get("from", "")).lower()

                # Only process emails that look like Greenhouse verification.
                if not (
                    any(k in subject for k in _SUBJECT_KEYWORDS)
                    or any(k in sender for k in _SENDER_KEYWORDS)
                ):
                    continue

                # Reject emails older than when we started polling.
                import email.utils as _eutils
                date_str = msg.get("date", "")
                try:
                    email_epoch = _eutils.parsedate_to_datetime(date_str).timestamp()
                    if email_epoch < min_email_epoch:
                        continue  # old email from a previous run
                except Exception:
                    pass  # can't parse date → let it through

                # Collect text body — prefer text/plain, fall back to
                # tag-stripped text/html (Greenhouse sends HTML-only emails).
                plain_body = ""
                html_body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        ct = part.get_content_type()
                        payload = part.get_payload(decode=True)
                        if not payload:
                            continue
                        decoded = payload.decode(
                            part.get_content_charset() or "utf-8",
                            errors="replace",
                        )
                        if ct == "text/plain":
                            plain_body += decoded
                        elif ct == "text/html":
                            html_body += decoded
                else:
                    payload = msg.get_payload(decode=True)
                    if payload:
                        decoded = payload.decode(
                            msg.get_content_charset() or "utf-8", errors="replace"
                        )
                        if msg.get_content_type() == "text/html":
                            html_body = decoded
                        else:
                            plain_body = decoded

                # If no plain text, convert HTML to plain by stripping tags.
                if not plain_body and html_body:
                    plain_body = _strip_html(html_body)

                if not plain_body:
                    continue

                # Line-by-line extraction: find a line that ends with ":"
                # (after keyword text like "your application:"), then take
                # the very next non-blank line as the code.
                code = _extract_code_from_plain_text(plain_body)
                if code:
                    mail.logout()
                    return code

            mail.logout()
        except Exception as exc:
            log.debug("IMAP poll error: %s", exc)

        remaining = deadline - time.time()
        if remaining <= 0:
            break
        time.sleep(min(poll_interval, remaining))

    log.warning("IMAP: no verification code found within %ds", timeout)
    return None


def _detect_captcha(page: Any) -> bool:
    """Return True only if a CAPTCHA challenge is *actively blocking* the user.

    Intentionally NOT triggered by:
    - reCAPTCHA v3 script tags (invisible, background-only, present on every
      Greenhouse / Lever form — does not require user interaction)
    - hCaptcha widget EMBED — Lever embeds hCaptcha on every apply page as a
      background widget; simply seeing "hcaptcha.com" in page source is NOT
      evidence of a blocking challenge.  Only the popup challenge iframe counts.
    - Any other analytics / bot-detection JS that doesn't present a challenge

    Triggered by:
    - Cloudflare interstitial / "Just a moment" page
    - reCAPTCHA v2 interactive checkbox (api2/anchor or api2/bframe iframe)
    - hCaptcha POPUP challenge frame (src contains "frame=challenge" or "/challenge")
    - Explicit blocking overlay text ("please verify you are human", etc.)
    """
    try:
        content = page.content().lower()
    except Exception:
        return False

    # Cloudflare interstitial
    if "cf-challenge-running" in content:
        return True
    if "window._cf_chl" in content:
        return True
    if "just a moment" in content and "cloudflare" in content:
        return True

    # reCAPTCHA v2 interactive challenge iframes (NOT the api.js script tag)
    if "recaptcha/api2/anchor" in content:
        return True
    if "recaptcha/api2/bframe" in content:
        return True

    # hCaptcha POPUP challenge iframe.
    # Lever embeds hCaptcha on every apply page as a background widget; that
    # alone does NOT constitute a blocking challenge.  The POPUP challenge frame
    # has "frame=challenge" in its src.  Only flag when that specific frame
    # is rendered — or when the hCaptcha checkbox widget is explicitly shown
    # (src contains "/captcha/v1/.../frame=checkbox").
    #
    # Implementation: use DOM query rather than raw-content string search so
    # we only flag when the frame is actually present in the live DOM.
    try:
        challenge_iframes = page.locator(
            'iframe[src*="hcaptcha"][src*="frame=challenge"],'
            'iframe[src*="hcaptcha"][src*="/challenge"]'
        )
        if challenge_iframes.count() > 0:
            return True
    except Exception:
        pass

    # Explicit blocking challenge phrases (body text, not raw HTML)
    _BLOCKING_PHRASES = (
        "please verify you are human",
        "complete the security check",
        "verify you are not a robot",
        "press and hold to confirm",
        "access to this page has been denied",
    )
    if any(phrase in content for phrase in _BLOCKING_PHRASES):
        return True

    return False


def _fill_lever_cards(
    page: Any, already_filled_names: set[str], field_errors: list[str]
) -> None:
    """Discover and fill Lever qualifying-question card fields at Playwright time.

    Lever's public posting API often omits `customQuestions` (they are returned
    as empty lists), but the actual apply page renders them via the SPA.  These
    fields are identified by `name="cards[UUID][fieldN]"`.

    Strategy:
    1. Find every unique `cards[...]` name that is NOT already in our
       pre-resolved data (``already_filled_names``).
    2. Extract the question text from the ``<li class="application-question">``
       parent — specifically the ``.application-label .text`` inner text.
    3. Apply rule-based heuristics (via ``_card_heuristic_answer``) to determine
       the answer.  Unknown questions are left unfilled and logged as
       ``card_unknown:<name>``.
    4. Fill radio / select fields using the existing ``_fill_field`` logic.
    """
    try:
        # Build a map from card field name → question text using DOM.
        card_info: dict[str, dict] = page.evaluate(
            """() => {
                const result = {};
                const seenNames = new Set();
                document.querySelectorAll('[name]').forEach(el => {
                    const name = el.getAttribute('name');
                    if (!name || !name.startsWith('cards[')) return;
                    if (el.getAttribute('type') === 'hidden') return;
                    if (seenNames.has(name)) return;
                    seenNames.add(name);

                    const tag = el.tagName.toLowerCase();
                    const type = el.getAttribute('type') || tag;

                    // Walk up to <li class="application-question">
                    let node = el;
                    let questionText = '';
                    while (node && node.parentElement) {
                        node = node.parentElement;
                        if (node.classList && node.classList.contains('application-question')) {
                            const textEl = node.querySelector('.application-label .text');
                            if (textEl) {
                                // Clone and strip the required asterisk span
                                const clone = textEl.cloneNode(true);
                                clone.querySelectorAll('span.required').forEach(s => s.remove());
                                questionText = clone.textContent.trim();
                            }
                            break;
                        }
                    }

                    let options = [];
                    if (tag === 'select') {
                        options = Array.from(el.querySelectorAll('option'))
                                      .map(o => o.textContent.trim())
                                      .filter(o => o && o !== 'Select...');
                    }

                    result[name] = {type, questionText, options};
                });
                return result;
            }"""
        )
    except Exception as exc:
        log.debug("_fill_lever_cards: JS evaluation failed: %s", exc)
        return

    for name, info in card_info.items():
        if name in already_filled_names:
            continue

        q_text = info.get("questionText", "")
        q_type = info.get("type", "")
        options = info.get("options", [])

        answer = _card_heuristic_answer(q_text, q_type, options)
        if answer is None:
            log.debug("_fill_lever_cards: no heuristic answer for %r (q=%r)", name, q_text)
            field_errors.append(f"card_unknown:{name}")
            continue

        try:
            _fill_field(page, name, answer)
            log.debug("_fill_lever_cards: filled %r=%r (q=%r)", name, answer, q_text)
            _jitter(0.05, 0.15)
        except Exception as exc:
            log.debug("_fill_lever_cards: fill error for %r: %s", name, exc)
            field_errors.append(f"fill_card:{name}:{type(exc).__name__}")


def _card_heuristic_answer(q_text: str, q_type: str, options: list[str]) -> str | None:
    """Return a heuristic answer for a Lever card qualifying question.

    Uses the question text and available options to determine the best answer.
    Returns None when no confident answer can be produced.
    """
    lo = q_text.lower()

    # ── US Citizen / work authorization ────────────────────────────────────
    if any(w in lo for w in ("citizen", "eligible to work", "authorized to work",
                              "work authorization", "legally authorized")):
        if q_type == "radio":
            for opt in options:
                if opt.strip().lower() == "yes":
                    return "Yes"
        return "Yes"

    # ── Visa / sponsorship required ─────────────────────────────────────────
    if any(w in lo for w in ("sponsorship", "require.*visa", "visa.*require",
                              "need.*visa", "work.*visa")):
        if q_type == "radio":
            for opt in options:
                if opt.strip().lower() == "no":
                    return "No"
        return "No"

    # ── Security clearance (select) ─────────────────────────────────────────
    if any(w in lo for w in ("clearance", "security clearance", "ts/sci", "top secret")):
        # Prefer the "No clearance" option; fall back to the last option (usually no/none).
        for opt in options:
            opt_lower = opt.lower()
            if any(x in opt_lower for x in ("no clearance", "none", "not current")):
                return opt
        # Last non-empty option is usually the most restrictive / "none" option.
        if options:
            return options[-1]

    # ── Willing to relocate ──────────────────────────────────────────────────
    if any(w in lo for w in ("relocation", "willing to relocate", "open to relocation")):
        return "Yes"

    # ── Remote work preference (select or radio) ─────────────────────────────
    if "remote" in lo and "prefer" in lo:
        for opt in options:
            if "remote" in opt.lower():
                return opt

    # ── Start date ───────────────────────────────────────────────────────────
    if any(w in lo for w in ("start date", "when can you start", "earliest start")):
        return "May 2026"

    # ── How did you hear ──────────────────────────────────────────────────────
    if any(w in lo for w in ("how did you hear", "how did you find", "referral source")):
        if options:
            for opt in options:
                if any(x in opt.lower() for x in ("job board", "linkedin", "online", "internet")):
                    return opt
        return "Online job board"

    # ── Yes/No generic (any remaining required boolean) ──────────────────────
    if q_type == "radio" and set(o.lower() for o in options) == {"yes", "no"}:
        # Default yes for positively-framed questions
        if any(w in lo for w in ("able", "willing", "open to", "have you", "do you")):
            return "Yes"

    return None


def _inner_text_safe(page: Any) -> str:
    try:
        return page.inner_text("body") or ""
    except Exception:
        try:
            return page.content()
        except Exception:
            return ""


def _collect_page_errors(page: Any) -> str:
    """Scrape VISIBLE error messages from the current page.

    Only looks at elements that are actually visible to the user — prevents
    hidden error nodes (e.g., Lever's file-size tooltip that is always in the
    DOM but shown only on hover / trigger) from polluting the error field.
    """
    selectors = [
        ".error:visible",
        ".alert-error:visible",
        ".alert-danger:visible",
        '[class*="error"]:visible',
        ".invalid-feedback:visible",
        '[data-error]:visible',
        ".field-error:visible",
        ".form-error:visible",
    ]
    msgs: list[str] = []
    for sel in selectors:
        try:
            for loc in page.locator(sel).all()[:3]:
                # Double-check visibility via is_visible() in case the CSS
                # :visible pseudo-class isn't supported by all Playwright builds.
                if not loc.is_visible():
                    continue
                txt = loc.inner_text().strip()
                if txt and txt not in msgs:
                    msgs.append(txt[:120])
        except Exception:
            pass
    return "; ".join(msgs[:5])


def _jitter(low: float, high: float) -> None:
    """Sleep for a random duration in [low, high] seconds."""
    time.sleep(random.uniform(low, high))
