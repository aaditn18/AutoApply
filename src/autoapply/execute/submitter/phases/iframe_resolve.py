"""Detect Greenhouse / Lever iframe wrappers and redirect to the embed URL.

Many Greenhouse customers route applications through a custom apply
domain — Lyft on ``app.careerpuck.com``, others on company-owned
careers sites — that load the standard Greenhouse form inside an
``<iframe>`` pointing to ``job-boards.greenhouse.io/embed/job_app``
or similar. Same for some Lever wrappers.

Our submitter's ``page.locator(...)`` calls operate on the top-level
document only — they never enter iframes. The result on iframe
wrappers is that every selector finds nothing and the form looks
"empty" to the fill phases.

The pragmatic fix here is to **redirect the top-level browser** to
the iframe's own URL. The Greenhouse embed page is a fully working
form on its own; navigating to it directly puts every input back at
the top level where our existing selectors find them.

This module exposes one function, :func:`resolve_form_iframe`, which
the driver calls right after the initial ``page.goto(url)``:

  - If the page has a frame whose URL matches a known ATS embed
    pattern, return that URL (so the driver re-navigates).
  - Otherwise return None (no redirect needed).
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

log = logging.getLogger(__name__)


# Patterns whose presence inside a child frame signals "this is the
# real form, navigate me there." Each pattern is a substring match
# against ``frame.url``.
_EMBED_PATTERNS: tuple[str, ...] = (
    "greenhouse.io/embed/job_app",
    "boards.greenhouse.io/embed",
    "job-boards.greenhouse.io/embed",
    "jobs.lever.co/",         # Lever wrappers (rare, but exist)
)


def _looks_like_form_frame(frame_url: str) -> bool:
    if not frame_url or frame_url == "about:blank":
        return False
    return any(pat in frame_url for pat in _EMBED_PATTERNS)


def _is_known_wrapper_host(page_url: str) -> bool:
    """Heuristic: only spend time looking for an embed iframe when
    the top-level URL looks like a known wrapper host.

    Without this gate, every standard Greenhouse / Lever / Ashby
    apply page would burn 12 seconds waiting for an iframe that's
    never going to appear.
    """
    if not page_url:
        return False
    direct_hosts = (
        "boards.greenhouse.io",
        "job-boards.greenhouse.io",
        "jobs.lever.co",
        "ashbyhq.com",
    )
    if any(h in page_url for h in direct_hosts):
        return False
    # Anything else is a candidate — careerpuck.com, company-owned
    # career sites that embed Greenhouse, etc.
    return True


def resolve_form_iframe(page: Any, *, settle_seconds: float = 12.0) -> str | None:
    """Return the URL of an embedded ATS form iframe, or None.

    Short-circuits if the top-level page is a direct ATS host
    (boards.greenhouse.io, jobs.lever.co, ashbyhq.com) — those forms
    are flat by definition and waiting 12s for an iframe that will
    never appear is wasted time.

    Otherwise polls ``page.frames`` for up to ``settle_seconds``.
    Hosts like ``app.careerpuck.com`` take 5-8 seconds before their
    embed iframe appears.
    """
    try:
        page_url = page.url or ""
    except Exception:
        page_url = ""
    if not _is_known_wrapper_host(page_url):
        # Direct ATS host — no iframe expected. One quick check of
        # already-loaded frames in case we're wrong, then bail.
        try:
            for f in page.frames:
                furl = getattr(f, "url", "") or ""
                if _looks_like_form_frame(furl) and furl != page_url:
                    return furl
        except Exception:
            pass
        return None

    # Wrapper host — block on the top-level <iframe> element first
    # so we short-circuit when one shows up early, then poll
    # ``page.frames`` for the URL.
    try:
        page.wait_for_selector(
            "iframe", timeout=int(settle_seconds * 1000),
        )
    except Exception:
        pass

    deadline = time.monotonic() + settle_seconds
    while time.monotonic() < deadline:
        try:
            frames = list(page.frames)
        except Exception as exc:
            log.debug("iframe_resolve: page.frames failed: %s", exc)
            return None

        for f in frames:
            try:
                furl = f.url or ""
            except Exception:
                continue
            if _looks_like_form_frame(furl) and furl != page_url:
                log.info(
                    "iframe_resolve: found embedded form frame at %s", furl
                )
                return furl

        # No matching frame yet — let the SPA work for a moment.
        time.sleep(0.25)

    return None
