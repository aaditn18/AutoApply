"""Radio-button filling.

Check / uncheck for plain ``<input type="checkbox">`` is handled
inline in :func:`..dispatch.fill_field` — it's a one-liner (truthy
token → ``.check()``, else ``.uncheck()``).

Radio buttons are trickier because the ``name`` attribute groups them
and the matching has to consider both the ``value=`` attribute and the
associated ``<label>`` text. Each strategy is wrapped in its own
try/except so a DOM mutation mid-fill doesn't crash the whole
submission.
"""

from __future__ import annotations

from typing import Any


def fill_radio(page: Any, radio_group: Any, name: str, value: str) -> None:
    """Check the radio whose ``value=`` or label text matches ``value``.

    Strategy:
      1. Exact ``value=`` match (CSS attribute selector — fastest).
      2. Case-insensitive ``value=`` search (iterate over group).
      3. Case-insensitive label-text match via ``<label for="id">``.
    """
    v_lower = value.strip().lower()

    # 1. Exact value= attribute.
    exact_val = page.locator(
        f'input[type="radio"][name="{name}"][value="{value}"]'
    )
    if exact_val.count() > 0:
        exact_val.first.check(timeout=3_000)
        return

    # 2. Case-insensitive value= search.
    try:
        for radio in radio_group.all():
            rv = (radio.get_attribute("value") or "").strip().lower()
            if rv == v_lower:
                radio.check(timeout=3_000)
                return
    except Exception:
        pass

    # 3. By label text.
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
