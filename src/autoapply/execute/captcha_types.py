"""Captcha type detection — identify which captcha is blocking the form.

Different captcha systems (hCaptcha, reCAPTCHA v2/v3, Cloudflare Turnstile)
require different solver APIs on 2Captcha / Anti-Captcha / etc. Before we
can dispatch to the right solver, we need to know what we're looking at.

This module answers one question: *"what captcha is currently visible on
the page, and how should we solve it?"* It returns a `CaptchaDetection`
with a canonical `kind` string plus the extracted site-key, which the
dispatcher in `playwright_submit._maybe_solve_and_retry_captcha` uses to
pick the right solver function.

For hCaptcha specifically, we distinguish two sub-kinds:
  - `hcaptcha_image`  — an active image-grid puzzle is visible
                        ("Please click each image containing a motorcycle.").
                        These require the Coordinates method on 2Captcha
                        when the token method is gated on the account.
  - `hcaptcha_token`  — widget is present but no active puzzle (invisible
                        or checkbox-only). Use the token API.

Supported kinds and their 2Captcha/JSON-API equivalents:

    kind              | 2Captcha method     | JSON API task type
    ------------------+---------------------+----------------------------
    hcaptcha_image    | post+coords         | (coords only)
    hcaptcha_token    | hcaptcha            | HCaptchaTaskProxyless
    recaptcha_v2      | userrecaptcha       | RecaptchaV2TaskProxyless
    recaptcha_v3      | userrecaptcha+v3    | RecaptchaV3TaskProxyless
    turnstile         | turnstile           | AntiTurnstileTaskProxyless
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse


log = logging.getLogger(__name__)


# Any of these phrases inside an hCaptcha iframe means an active image
# puzzle is being shown. Kept narrow — must not fire on the idle
# "I am human" checkbox widget.
_HCAPTCHA_PUZZLE_PHRASES: tuple[str, ...] = (
    "please click each image containing",
    "please click on all images",
    "please click each image",
    "select all images",
    "please select all",
    "click the images",
    "click each image",
    "click each picture",
)


@dataclass
class CaptchaDetection:
    """Classification result for the captcha currently blocking the page."""

    kind: str                                  # see module docstring for values
    site_key: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def needs_coords(self) -> bool:
        """True if this captcha must be solved via image-click (coordinates).

        Currently only `hcaptcha_image` qualifies.
        """
        return self.kind == "hcaptcha_image"


def detect_captcha(page: Any) -> CaptchaDetection | None:
    """Identify the captcha currently visible on the page.

    Returns None if no recognized captcha is detectable. The dispatcher
    should treat None as "can't solve automatically → route to review".

    Order of checks matters: Turnstile before reCAPTCHA before hCaptcha so
    the first match wins on pages that (rarely) embed two systems. Each
    check is defensive — exceptions bubble up as "not that type".
    """
    # ── 1. Cloudflare Turnstile ─────────────────────────────────────────
    # Iframe src host is `challenges.cloudflare.com`; site-key lives on a
    # `.cf-turnstile[data-sitekey]` div in the page DOM.
    if _has_iframe(page, "challenges.cloudflare.com"):
        sk = (
            _get_sitekey(page, ".cf-turnstile, [data-sitekey]")
            or _sitekey_from_iframe_query(page, "challenges.cloudflare.com", key="k")
        )
        log.info("captcha detected: turnstile site_key=%s…", (sk or "?")[:12])
        return CaptchaDetection(kind="turnstile", site_key=sk)

    # ── 2. Google reCAPTCHA v2 ──────────────────────────────────────────
    # v2 interactive ships two iframes: `api2/anchor` (checkbox) and
    # `api2/bframe` (image challenge). Site-key is on a `.g-recaptcha`
    # div via `data-sitekey`.
    if _has_iframe(page, "google.com/recaptcha/api2"):
        sk = (
            _get_sitekey(page, ".g-recaptcha, [data-sitekey]")
            or _sitekey_from_iframe_query(page, "google.com/recaptcha", key="k")
        )
        log.info("captcha detected: recaptcha_v2 site_key=%s…", (sk or "?")[:12])
        return CaptchaDetection(kind="recaptcha_v2", site_key=sk)

    # ── 3. hCaptcha — distinguish image-puzzle vs. token ────────────────
    # hCaptcha embeds an "enclave" iframe on every page as a background
    # widget. The presence of an active image puzzle is determined by
    # scanning the iframe's inner text for puzzle phrases.
    if _has_iframe(page, "hcaptcha"):
        has_puzzle = _has_hcaptcha_image_puzzle(page)
        sk = (
            _get_sitekey(page, "[data-sitekey]")
            or _sitekey_from_iframe_query(page, "hcaptcha", key="sitekey")
        )
        kind = "hcaptcha_image" if has_puzzle else "hcaptcha_token"
        log.info("captcha detected: %s site_key=%s…", kind, (sk or "?")[:12])
        return CaptchaDetection(kind=kind, site_key=sk)

    log.warning("captcha detected but type could not be identified")
    return None


# ── Helpers ──────────────────────────────────────────────────────────────


def _has_iframe(page: Any, src_contains: str) -> bool:
    try:
        return page.locator(f'iframe[src*="{src_contains}"]').count() > 0
    except Exception:
        return False


def _has_hcaptcha_image_puzzle(page: Any) -> bool:
    """Scan all hCaptcha-origin frames' body text for an active puzzle prompt."""
    for fr in page.frames:
        url_l = (getattr(fr, "url", "") or "").lower()
        if "hcaptcha" not in url_l:
            continue
        try:
            text = fr.evaluate("() => (document.body?.innerText || '').toLowerCase()")
        except Exception:
            continue
        if not text:
            continue
        if any(p in text for p in _HCAPTCHA_PUZZLE_PHRASES):
            return True
    return False


def _get_sitekey(page: Any, selector: str) -> str:
    """Return the `data-sitekey` (or `data-site-key`) attribute of the first match."""
    try:
        loc = page.locator(selector)
        if loc.count() == 0:
            return ""
        el = loc.first
        for attr in ("data-sitekey", "data-site-key"):
            val = el.get_attribute(attr)
            if val:
                return str(val)
    except Exception:
        pass
    # Fallback via JS — catches shadow-DOM / dynamic attribute cases.
    try:
        v = page.evaluate(
            """(sel) => {
                const el = document.querySelector(sel);
                if (!el) return '';
                return el.getAttribute('data-sitekey')
                    || el.getAttribute('data-site-key')
                    || '';
            }""",
            selector,
        )
        return str(v or "")
    except Exception:
        return ""


def _sitekey_from_iframe_query(page: Any, host_substr: str, *, key: str) -> str:
    """Parse an iframe src's query-string for the given site-key param."""
    try:
        for fr_el in page.locator(f'iframe[src*="{host_substr}"]').all()[:6]:
            src = fr_el.get_attribute("src") or ""
            if not src:
                continue
            qs = parse_qs(urlparse(src).query)
            sk = qs.get(key, [None])[0]
            if sk:
                return str(sk)
    except Exception:
        pass
    return ""
