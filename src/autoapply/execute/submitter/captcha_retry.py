"""Smart captcha solver dispatcher + post-solve submit retry.

When :func:`captcha_detect.wait_for_captcha` returns True, the driver calls
:func:`maybe_solve_and_retry_captcha` which:

  - For ``CAPTCHA_SOLVER=2captcha_coords`` → always runs the Grid/Coords
    click-in-browser flow.
  - For ``CAPTCHA_SOLVER=2captcha`` → classifies the captcha via
    ``captcha_types.detect_captcha`` and auto-routes: hCaptcha (any variant)
    → Grid; reCAPTCHA v2 → token; Turnstile → token.
  - For other providers (anticaptcha / capsolver / capmonster) → token
    path for hCaptcha.

If a token is returned, :func:`inject_token_and_resubmit` writes it into the
response field(s) and re-clicks submit. If no solver is configured or the
solver fails, returns False — the driver then raises ``CaptchaDetected`` so
the whole application routes to the review queue.
"""

from __future__ import annotations

import logging
from typing import Any

from .util import jitter


log = logging.getLogger(__name__)


def maybe_solve_and_retry_captcha(
    page: Any,
    *,
    submit_selector: str,
    solver: str,
    api_key: str,
    timeout: int,
) -> bool:
    """Smart captcha solver dispatcher.

    Returns True on successful solve + re-submit; False means
    "route to review" for the caller.

    Provider semantics:
      "2captcha"         — auto-route: image puzzle → coords; other
                           supported kinds → token API (userrecaptcha /
                           turnstile / hcaptcha).
      "2captcha_coords"  — force the coords path for ANY captcha on the
                           page (useful when token hCaptcha is gated and
                           you know the site will show an image puzzle).
      "anticaptcha"      — token API for everything it supports (hCaptcha
                           most notably — no account gating).
      "capsolver"        — token API (note: proxy-less hCaptcha not
                           supported for many sites).
      "capmonster"       — token API.
    """
    if not solver or not api_key:
        log.info("captcha detected but no solver configured — routing to review")
        return False

    from autoapply.execute.captcha_types import detect_captcha

    # Explicit force-coords path — skip detection, always run coords.
    if solver == "2captcha_coords":
        return solve_via_coords_path(page, api_key=api_key, timeout=timeout)

    detection = detect_captcha(page)
    if detection is None:
        log.warning("captcha detected but type could not be classified — review queue")
        return False

    # Auto-route 2Captcha based on the detected captcha kind.
    # IMPORTANT: 2Captcha dropped hCaptcha from their API entirely (verified
    # against their current docs 2026-04-18 — hCaptcha is not listed on
    # https://2captcha.com/api-docs). Every hCaptcha variant — whether an
    # active image grid OR an invisible/checkbox token — must therefore be
    # routed through the Grid path (GridTask), because that's the only
    # 2Captcha endpoint that can resolve hCaptcha today. The Grid solver
    # polls for the puzzle to appear if one isn't visible yet.
    if solver == "2captcha":
        if detection.kind in ("hcaptcha_image", "hcaptcha_token"):
            log.info(
                "2captcha auto-route → Grid (hCaptcha, kind=%s; "
                "2Captcha no longer supports hCaptcha token solving)",
                detection.kind,
            )
            return solve_via_coords_path(page, api_key=api_key, timeout=timeout)
        return solve_via_2captcha_token(
            page,
            submit_selector=submit_selector,
            detection=detection,
            api_key=api_key,
            timeout=timeout,
        )

    # Third-party token solvers (anticaptcha / capsolver / capmonster) —
    # they currently only support hCaptcha in this codebase. Future work
    # could extend them to reCAPTCHA / Turnstile; for now if the kind
    # isn't hcaptcha we fall through to review.
    if detection.kind not in ("hcaptcha_image", "hcaptcha_token"):
        log.warning(
            "solver=%s does not currently support captcha kind=%s — review queue",
            solver, detection.kind,
        )
        return False

    if not detection.site_key:
        log.warning(
            "solver=%s needs site-key but none could be extracted — review queue",
            solver,
        )
        return False

    try:
        from autoapply.execute.captcha_solver import solve_hcaptcha
        log.info("submitting hCaptcha to solver=%s site_key=%s…",
                 solver, detection.site_key[:8])
        token = solve_hcaptcha(
            site_key=detection.site_key,
            page_url=page.url,
            provider=solver,
            api_key=api_key,
            timeout=timeout,
        )
    except Exception as exc:
        log.warning("captcha solver failed: %s — routing to review", exc)
        return False

    return inject_token_and_resubmit(
        page, submit_selector=submit_selector,
        token=token, field_names=("h-captcha-response", "g-recaptcha-response"),
    )


def solve_via_coords_path(page: Any, *, api_key: str, timeout: int) -> bool:
    """Run the 2Captcha Coordinates in-browser click flow; wait for auto-submit."""
    from autoapply.execute.captcha_coords import solve_hcaptcha_coords
    try:
        solved = solve_hcaptcha_coords(page, api_key=api_key, total_timeout=timeout)
    except Exception as exc:
        log.warning("coords solver raised: %s", exc)
        return False
    if not solved:
        return False
    # hCaptcha's success callback auto-submits Lever's form once the
    # challenge closes cleanly — no explicit re-click required. Wait
    # for the resulting network activity before success detection.
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:
        pass
    jitter(1.0, 2.0)
    return True


def solve_via_2captcha_token(
    page: Any,
    *,
    submit_selector: str,
    detection: Any,                 # captcha_types.CaptchaDetection
    api_key: str,
    timeout: int,
) -> bool:
    """Solve via 2Captcha's token APIs (``userrecaptcha`` / ``turnstile``)
    based on the detected captcha kind, then inject the token into the
    matching response field and re-click submit."""
    if not detection.site_key:
        log.warning("2captcha token path needs site-key for kind=%s but "
                    "none was extracted — review queue", detection.kind)
        return False

    page_url = page.url

    try:
        from autoapply.execute import captcha_solver as cs

        if detection.kind == "recaptcha_v2":
            log.info("2captcha token → recaptcha_v2 site_key=%s…",
                     detection.site_key[:12])
            token = cs.solve_recaptcha_v2_2captcha(
                site_key=detection.site_key, page_url=page_url,
                api_key=api_key, timeout=timeout,
            )
            field_names = ("g-recaptcha-response",)

        elif detection.kind == "turnstile":
            log.info("2captcha token → turnstile site_key=%s…",
                     detection.site_key[:12])
            token = cs.solve_turnstile_2captcha(
                site_key=detection.site_key, page_url=page_url,
                api_key=api_key, timeout=timeout,
            )
            field_names = ("cf-turnstile-response",)

        else:
            # hcaptcha_* is handled by the Grid path upstream — this branch
            # is only reached for captcha kinds we haven't wired to 2Captcha.
            log.warning("2captcha token path unsupported for kind=%s",
                        detection.kind)
            return False

    except Exception as exc:
        log.warning("2captcha solver failed for kind=%s: %s — review queue",
                    detection.kind, exc)
        return False

    return inject_token_and_resubmit(
        page, submit_selector=submit_selector,
        token=token, field_names=field_names,
    )


def inject_token_and_resubmit(
    page: Any,
    *,
    submit_selector: str,
    token: str,
    field_names: tuple[str, ...],
) -> bool:
    """Write the solver-returned token into every matching response field
    (textarea or hidden input), dispatch input/change events, then re-click
    the submit button and wait for the page to settle."""
    try:
        page.evaluate(
            """({tok, names}) => {
                names.forEach(n => {
                    const sel = 'textarea[name="' + n + '"], input[name="' + n + '"]';
                    document.querySelectorAll(sel).forEach(el => {
                        el.value = tok;
                        el.dispatchEvent(new Event('input',  {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                    });
                });
            }""",
            {"tok": token, "names": list(field_names)},
        )
        log.info("injected captcha token (len=%d) into fields=%s; re-clicking submit",
                 len(token), field_names)
        jitter(0.8, 1.5)
        page.click(submit_selector, timeout=10_000)
        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass
        jitter(1.0, 2.0)
        return True
    except Exception as exc:
        log.warning("post-solve inject/click failed: %s", exc)
        return False
