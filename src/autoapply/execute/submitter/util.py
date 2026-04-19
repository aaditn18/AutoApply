"""Leaf helpers shared across every submitter module.

None of these depend on other submitter modules — they're the bottom of
the import graph, safe to import everywhere.
"""

from __future__ import annotations

import random
import time
from typing import Any


def jitter(low: float, high: float) -> None:
    """Sleep for a random duration in ``[low, high]`` seconds.

    Used between user-like actions (clicks, fills, submits) to avoid
    machine-gun-timing patterns that bot detectors look for.
    """
    time.sleep(random.uniform(low, high))


def inner_text_safe(page: Any) -> str:
    """Return ``page.inner_text("body")`` with graceful fallback to content()."""
    try:
        return page.inner_text("body") or ""
    except Exception:
        try:
            return page.content()
        except Exception:
            return ""


def strip_html(html: str) -> str:
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


def react_set_value(page: Any, element_handle: Any, value: str) -> None:
    """Set a React-controlled input value and fire the synthetic events React needs.

    React uses a custom setter on HTMLInputElement.prototype so that synthetic
    onChange fires. A plain ``el.value = …`` assignment bypasses that setter
    and leaves the button disabled. We must call the original setter explicitly.
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
