"""Pre-submit captcha token + settle-time phase.

Ashby (and a few Greenhouse tenants) run an invisible reCAPTCHA v3 /
Turnstile widget that populates a response token AFTER enough
human-like interactions have happened on the page. Our headless
pipeline sometimes clicks submit before that token is populated,
which Ashby's backend flags as "possible spam".

This phase sits right before ``click_submit_and_handle_captcha`` and:

1. Sleeps briefly to let invisible reCAPTCHA / Turnstile finish
   generating its token after our final form fills.
2. Detects the captcha kind on the page via
   :func:`execute.captcha_types.detect_captcha`.
3. Checks whether the expected response field
   (``g-recaptcha-response`` / ``cf-turnstile-response`` /
   ``h-captcha-response``) has a value. If yes, we're good — submit
   will carry the token naturally.
4. If empty AND a 2Captcha-style solver is configured, calls the
   solver (``solve_recaptcha_v2_2captcha`` / ``solve_turnstile_2captcha``)
   and injects the returned token into the response field.
5. No-ops when no captcha is detected (most Greenhouse pages).

A successful token (either auto-generated or solver-injected) means
the subsequent submit click should carry a valid token and avoid
Ashby's spam gate. If injection fails, we still let the submit fire
and surface whatever error Ashby returns for post-mortem.
"""

from __future__ import annotations

import logging
import time
from typing import Any


log = logging.getLogger(__name__)


# Map detected captcha kind → the DOM response field name the token
# gets written into. Matches the conventions in
# ``captcha_retry.py::inject_token_and_resubmit``.
_RESPONSE_FIELD: dict[str, tuple[str, ...]] = {
    "recaptcha_v2": ("g-recaptcha-response",),
    # reCAPTCHA v3 uses the same textarea name; distinguished only
    # by how the token is obtained (action parameter on solve).
    "recaptcha_v3": ("g-recaptcha-response",),
    "hcaptcha_token": ("h-captcha-response",),
    "hcaptcha_image": ("h-captcha-response",),
    "turnstile": ("cf-turnstile-response",),
}


def ensure_captcha_token(
    page: Any,
    *,
    solver: str,
    api_key: str,
    timeout: int,
    settle_seconds: float = 3.0,
) -> None:
    """Wait for (and if needed, solve) the captcha token before submit.

    No-op when no captcha is detected on the page. No-op when the
    response field is already populated. Logs + returns gracefully
    on any solver failure — the caller still fires the submit click.
    """
    from autoapply.execute.captcha_types import detect_captcha

    # Settle — let invisible reCAPTCHA / Turnstile JS populate the
    # response field after our last form fill. Ashby's v3 widget
    # typically generates a token within 1-2 s of the last input
    # event; 3 s is a safe headroom.
    if settle_seconds > 0:
        time.sleep(settle_seconds)

    detection = detect_captcha(page)
    if detection is None:
        return

    field_names = _RESPONSE_FIELD.get(detection.kind) or ()
    if not field_names:
        log.debug(
            "pre-submit captcha: kind=%s has no response-field mapping",
            detection.kind,
        )
        return

    # Check if the token is already populated (invisible reCAPTCHA
    # usually fills it automatically).
    existing = _read_token(page, field_names)
    if existing:
        log.info(
            "pre-submit captcha: %s already populated (len=%d) — skipping solver",
            field_names[0], len(existing),
        )
        return

    if not solver or not api_key:
        log.warning(
            "pre-submit captcha: %s empty and no solver configured — "
            "submit will likely be flagged as spam",
            field_names[0],
        )
        return

    if not detection.site_key:
        log.warning(
            "pre-submit captcha: site-key not extracted for kind=%s — "
            "skipping solver",
            detection.kind,
        )
        return

    token = _solve(detection, page_url=page.url, api_key=api_key, timeout=timeout)
    if not token:
        return

    _inject_token(page, field_names, token)


def _read_token(page: Any, field_names: tuple[str, ...]) -> str:
    """Return the current value of the first matching response field."""
    for name in field_names:
        try:
            val = page.evaluate(
                """(n) => {
                    const el = document.querySelector(
                        `textarea[name="${n}"], input[name="${n}"]`
                    );
                    return el ? (el.value || '') : '';
                }""",
                name,
            )
            if val:
                return str(val)
        except Exception:
            continue
    return ""


def _solve(detection: Any, *, page_url: str, api_key: str, timeout: int) -> str:
    """Dispatch to the right 2Captcha solver based on captcha kind."""
    from autoapply.execute import captcha_solver as cs

    try:
        if detection.kind == "recaptcha_v2":
            log.info(
                "pre-submit captcha: solving recaptcha_v2 site_key=%s…",
                detection.site_key[:12],
            )
            return cs.solve_recaptcha_v2_2captcha(
                site_key=detection.site_key,
                page_url=page_url,
                api_key=api_key,
                timeout=timeout,
            ) or ""
        if detection.kind == "turnstile":
            log.info(
                "pre-submit captcha: solving turnstile site_key=%s…",
                detection.site_key[:12],
            )
            return cs.solve_turnstile_2captcha(
                site_key=detection.site_key,
                page_url=page_url,
                api_key=api_key,
                timeout=timeout,
            ) or ""
        # hCaptcha / reCAPTCHA v3 not yet wired for pre-submit
        # solving in this codebase. Post-submit coords path handles
        # hCaptcha image puzzles via captcha_retry.py.
        log.warning(
            "pre-submit captcha: solver dispatch not wired for kind=%s",
            detection.kind,
        )
    except Exception as exc:
        log.warning("pre-submit captcha: solver raised — %s", exc)
    return ""


def _inject_token(page: Any, field_names: tuple[str, ...], token: str) -> None:
    """Write the token into every matching response field + fire change events."""
    for name in field_names:
        try:
            page.evaluate(
                """({name, token}) => {
                    const el = document.querySelector(
                        `textarea[name="${name}"], input[name="${name}"]`
                    );
                    if (!el) return false;
                    el.value = token;
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    return true;
                }""",
                {"name": name, "token": token},
            )
            log.info(
                "pre-submit captcha: injected token into %s (len=%d)",
                name, len(token),
            )
        except Exception as exc:
            log.warning(
                "pre-submit captcha: failed to inject into %s — %s",
                name, exc,
            )
