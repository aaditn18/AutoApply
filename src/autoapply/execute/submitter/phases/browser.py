"""Browser setup phase — Chromium launch, context, stealth injection.

The UA pool is loaded from ``state/rules/browser_pool.yml`` (see
:mod:`autoapply.rules`). Stealth injection supports both
playwright-stealth v1 (``stealth_sync``) and v2 (``Stealth().use_sync``)
APIs; either ImportError path falls through to "continue without".

Usage::

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, ctx, page = launch_browser_context(
            p, headless=True, pre_navigation_cookies=[...],
        )
        try:
            ...
        finally:
            ctx.close()
            browser.close()

The caller owns the ``sync_playwright`` lifetime so a single Playwright
driver instance can spin up multiple submissions in a loop without
paying the cold-start cost each time (future optimization; today the
caller is ``driver.submit_form`` which opens one Playwright per call).
"""

from __future__ import annotations

import logging
import random
from typing import Any

from autoapply.rules import load_rules


log = logging.getLogger(__name__)


# Chromium user-agent pool loaded once at import — see
# ``state/rules/browser_pool.yml`` for the pool + update policy.
USER_AGENTS: tuple[str, ...] = tuple(load_rules("browser_pool")["user_agents"])


def launch_browser_context(
    playwright: Any,
    *,
    headless: bool,
    pre_navigation_cookies: list[dict[str, Any]] | None = None,
) -> tuple[Any, Any, Any]:
    """Launch Chromium + create a context + new page + apply stealth.

    Returns ``(browser, context, page)``. The caller is responsible for
    closing context and browser in a ``finally`` block.

    ``pre_navigation_cookies`` are injected before any navigation — used
    primarily for the hCaptcha accessibility-bypass cookie, which has to
    be present on the FIRST page load to take effect.
    """
    browser = playwright.chromium.launch(headless=headless)
    ctx = browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport={"width": 1280, "height": 900},
        locale="en-US",
        timezone_id="America/New_York",
        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
    )

    if pre_navigation_cookies:
        ctx.add_cookies(pre_navigation_cookies)

    page = ctx.new_page()
    _inject_stealth(page)
    return browser, ctx, page


def _inject_stealth(page: Any) -> None:
    """Best-effort stealth injection — tolerates both API versions.

    Supports playwright-stealth v1 (``stealth_sync``) and v2
    (``Stealth().use_sync``). Either missing path logs at DEBUG and
    continues — stealth is defense-in-depth, not a hard requirement.
    """
    try:
        from playwright_stealth import Stealth  # type: ignore[import]

        Stealth().use_sync(page)
        return
    except ImportError:
        pass
    except Exception:
        log.debug("playwright-stealth v2 init failed — falling back to v1")

    try:
        from playwright_stealth import stealth_sync  # type: ignore[import]

        stealth_sync(page)
    except ImportError:
        log.debug("playwright-stealth unavailable — continuing without")
    except Exception:
        log.debug("playwright-stealth failed — continuing without")
