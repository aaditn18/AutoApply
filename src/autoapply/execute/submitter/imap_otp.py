"""Email-verification OTP fetching + entry.

Some Greenhouse (and occasionally other) tenants require a one-time code
emailed to the applicant's address before accepting a submission. This
module:

1. Detects the OTP-entry stage (:func:`detect_email_verification`).
2. Polls IMAP for the code (:func:`fetch_imap_verification_code`).
3. Types the code into the page (:func:`enter_verification_code`), handling
   both the 8-box Greenhouse-SPA layout and single-input legacy layouts.
4. Clicks the verify/submit button (:func:`click_otp_submit`).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from .util import jitter, react_set_value, strip_html


log = logging.getLogger(__name__)


def detect_email_verification(page: Any) -> bool:
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


def enter_verification_code(page: Any, code: str) -> None:
    """Type the verification code into the OTP input and click verify/submit.

    Handles two layouts:

    Layout A — Greenhouse new SPA: 8 individual single-character inputs with
      ``id="security-input-0" … id="security-input-7"`` (``maxlength="1"``
      each). Must fill one character per box and trigger React events
      per keystroke.

    Layout B — single combined input (older Greenhouse / other ATS):
      ``input[maxlength="6|8"]``, ``input[autocomplete="one-time-code"]``,
      etc. Fill whole code at once via React native-setter.

    After filling, wait for the submit button to enable (up to 8 s), then
    click it. Force-click via JS as final fallback.
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
                    react_set_value(page, handle, char)
                else:
                    # Fallback: type the character (triggers keyboard events).
                    box.press("Backspace")
                    box.type(char, delay=60)
                jitter(0.04, 0.12)
            except Exception as exc:
                log.debug("OTP box %d fill error (char=%r): %s", i, char, exc)

        log.debug("filled all %d OTP boxes", chars_to_fill)
        jitter(0.3, 0.6)
        click_otp_submit(page)
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
            react_set_value(page, handle, code)
            log.debug("set OTP via React native-setter (sel=%r)", filled_sel)
        else:
            inp_el.type(code, delay=80)
            log.debug("set OTP via type() (sel=%r)", filled_sel)
    except Exception as exc:
        log.debug("OTP entry error: %s", exc)

    jitter(0.4, 0.8)
    click_otp_submit(page)


def click_otp_submit(page: Any) -> None:
    """Wait for the verify/submit button to enable, then click it.

    After OTP entry the page's React logic enables the submit button
    asynchronously. We poll for up to 8 s before falling back to a
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


def extract_code_from_plain_text(body: str) -> str | None:
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


def fetch_imap_verification_code(
    *,
    imap_server: str,
    imap_port: int,
    imap_email: str,
    imap_password: str,
    timeout: int = 90,
) -> str | None:
    """Poll an IMAP inbox for a Greenhouse verification code email.

    Scans the INBOX for the most recent unread email from Greenhouse sent
    within the last ``timeout`` seconds and extracts the numeric/alphanumeric
    OTP from the message body.

    Returns the code string if found, or ``None`` on timeout / error.
    """
    import email as email_lib
    import imaplib
    import re
    from datetime import datetime, timedelta

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
    _CODE_RE = re.compile(  # noqa: F841  (kept for readers; extraction uses extract_code_from_plain_text)
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
                    plain_body = strip_html(html_body)

                if not plain_body:
                    continue

                # Line-by-line extraction: find a line that ends with ":"
                # (after keyword text like "your application:"), then take
                # the very next non-blank line as the code.
                code = extract_code_from_plain_text(plain_body)
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
