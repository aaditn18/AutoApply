"""File input handling — upload_file + file_input_selector.

Prefers the browser's native file-chooser API so that React upload widgets
(Lever, some Greenhouse tenants) receive the events their state machines
listen for. Plain ``set_input_files()`` bypasses those handlers and causes
spurious "file too large" / "invalid file" errors.
"""

from __future__ import annotations

from typing import Any


def upload_file(page: Any, selector: str, abs_path: str) -> None:
    """Upload a file to a file input, preferring the file-chooser API.

    The file-chooser API (``expect_file_chooser → click → set_files``) fires
    all browser-native events including the ones that React upload widgets
    (Lever, some Greenhouse tenants) listen to for state management.
    A plain ``set_input_files()`` call bypasses these handlers, which causes
    React to show spurious "file too large" or "invalid file" errors.

    Falls back to ``set_input_files()`` if the chooser dialog does not open
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


def file_input_selector(page: Any, name: str) -> str | None:
    """Return a CSS selector that locates the file ``<input>`` for the given field name.

    Tries ``name=`` attribute first, then ``id=`` attribute (new Greenhouse
    job-boards SPA omits name attributes). Returns ``None`` if nothing is found.
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
