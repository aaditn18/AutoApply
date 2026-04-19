"""Probe the page for an ACTIVELY BLOCKING captcha challenge.

Crucially NOT triggered by the background invisible reCAPTCHA v3 script,
hCaptcha's idle checkbox widget, or any other non-challenging bot-scoring
layer — those live on every Greenhouse / Lever form and do not require
human interaction.

:func:`detect_captcha` returns True only when one of these is present:
  - Cloudflare interstitial / "Just a moment".
  - reCAPTCHA v2 interactive checkbox (``api2/anchor`` or ``api2/bframe``).
  - hCaptcha POPUP challenge frame (``frame=challenge`` / ``/challenge``).
  - Visible hCaptcha iframe at modal dimensions (>400×400).
  - Explicit blocking overlay text ("please verify you are human", etc.).

:func:`wait_for_captcha` wraps this with a polling window because hCaptcha's
challenge modal can take several seconds to render after the submit click.

:func:`extract_hcaptcha_site_key` pulls the site-key out of the page DOM
(``data-sitekey`` attribute) or from the hCaptcha iframe's query string —
used by the solver dispatcher.
"""

from __future__ import annotations

import time
from typing import Any


def detect_captcha(page: Any) -> bool:
    """Return True only if a CAPTCHA challenge is *actively blocking* the user.

    See module docstring for the precise signals.
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

    # hCaptcha blocking challenge detection.
    # Lever embeds hCaptcha as a background "enclave" iframe on every apply
    # page — that alone is NOT a challenge. A blocking challenge appears as:
    #   (a) iframe src contains "frame=challenge" / "/challenge" /
    #       "frame=challenge-expanded" / "frame=challenge-hl", OR
    #   (b) a visible hCaptcha iframe that has been resized to challenge
    #       dimensions (>400 px tall — the widget is ~80 px, the challenge
    #       modal is 500+ px), OR
    #   (c) a challenge-puzzle text phrase appears inside the frame contents
    #       ("please click each image containing …").
    try:
        challenge_iframes = page.locator(
            'iframe[src*="hcaptcha"][src*="frame=challenge"],'
            'iframe[src*="hcaptcha"][src*="/challenge"]'
        )
        if challenge_iframes.count() > 0:
            return True
    except Exception:
        pass

    # Any visible hCaptcha iframe at MODAL size is almost certainly a
    # challenge popup. Idle checkbox widgets are typically ~300×74; the
    # idle "I am human" expanded widget can be ~300×300 on some Lever
    # boards, so we set the threshold at 400×400 (real challenge modals
    # are ~500×570+).
    try:
        for frame_el in page.locator('iframe[src*="hcaptcha"]').all()[:6]:
            if not frame_el.is_visible():
                continue
            box = frame_el.bounding_box()
            if box and box.get("height", 0) >= 400 and box.get("width", 0) >= 400:
                return True
    except Exception:
        pass

    # Look inside each hCaptcha-owned frame for challenge-puzzle prompts.
    # Keep the list narrow — only phrases that appear on an ACTIVE puzzle,
    # never on the idle checkbox widget. "Verify you are human" / "I am
    # human" live on the idle widget and must NOT be in this list.
    _HCAPTCHA_CHALLENGE_PHRASES = (
        "please click each image containing",
        "please click on all images",
        "please click each image",
        "select all images",
        "please select all",
        "click the images",
        "click each image",
        "click each picture",
    )
    try:
        for fr in page.frames:
            url_l = (getattr(fr, "url", "") or "").lower()
            if "hcaptcha" not in url_l:
                continue
            try:
                frame_text = fr.evaluate(
                    "() => (document.body && document.body.innerText) || ''"
                )
            except Exception:
                continue
            tl = (frame_text or "").lower()
            if any(p in tl for p in _HCAPTCHA_CHALLENGE_PHRASES):
                return True
    except Exception:
        pass

    # hCaptcha's modal container — when the challenge is open, a DIV with
    # role="dialog" + aria-modal=true exists in the main document. Some
    # sites render this without an iframe we can read cross-origin.
    try:
        modal = page.locator(
            'div[role="dialog"][aria-modal="true"], '
            'div[class*="hcaptcha"][class*="challenge"]'
        )
        if modal.count() > 0 and modal.first.is_visible():
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


def wait_for_captcha(page: Any, *, timeout: float = 10.0, poll: float = 0.6) -> bool:
    """Poll :func:`detect_captcha` for up to ``timeout`` seconds.

    hCaptcha's challenge modal loads asynchronously after the submit click —
    the enclave iframe expands, images fetch, then the prompt text renders.
    A single :func:`detect_captcha` call right after submit often misses it.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if detect_captcha(page):
            return True
        time.sleep(poll)
    # One last check at the deadline.
    return detect_captcha(page)


def extract_hcaptcha_site_key(page: Any) -> str:
    """Find the hCaptcha site-key for the current page.

    Checks, in order:
      1. any element with ``data-sitekey`` attribute (standard hCaptcha div)
      2. any element with class="h-captcha" carrying ``data-sitekey``
      3. parse the ``sitekey=`` query parameter from any hcaptcha iframe src
    """
    try:
        # 1 + 2 — DOM attribute lookup.
        v = page.evaluate(
            """() => {
                const el = document.querySelector('[data-sitekey]');
                return el ? el.getAttribute('data-sitekey') : null;
            }"""
        )
        if v:
            return str(v)
    except Exception:
        pass

    # 3 — parse iframe src.
    try:
        from urllib.parse import urlparse, parse_qs
        for fr in page.locator('iframe[src*="hcaptcha"]').all()[:6]:
            src = fr.get_attribute("src") or ""
            if not src:
                continue
            qs = parse_qs(urlparse(src).query)
            sk = qs.get("sitekey", [None])[0]
            if sk:
                return sk
    except Exception:
        pass

    return ""
