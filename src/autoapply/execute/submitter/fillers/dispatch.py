"""The unified ``fill_field`` dispatcher.

Given a machine-name (or id) and a value, :func:`fill_field` finds the
first matching DOM element and hands it to the right specialized
filler. Every selector tries ``[name="..."]`` first, then ``[id="..."]``,
because the new Greenhouse job-boards SPA omits ``name`` attributes
and identifies fields by ``id`` only.

Dispatch order:

  1. ``<select>``          → :func:`..native_select.fill_select`
  2. ``<input type=radio>`` → :func:`..checkbox_radio.fill_radio`
  3. ``<input type=checkbox>`` → inline check/uncheck
  4. Everything else       → ``.fill()`` with a React-Select retry
                              via :func:`..react_select.fill_combobox`
"""

from __future__ import annotations

import logging
from typing import Any

from .checkbox_radio import fill_radio
from .detect import _is_react_select
from .native_select import fill_select
from .react_select import fill_combobox


log = logging.getLogger(__name__)


def fill_field(page: Any, name: str, value: str) -> None:
    """Fill one form field identified by its ``name`` or ``id`` attribute.

    Raises :class:`ValueError` if no matching element is found — the
    caller (``phases.api_fill``) distinguishes "no element found" from
    other fill errors so missing SPA-injected fields don't pollute the
    ``field_errors`` list.
    """
    # 1. <select> ---------------------------------------------------------
    actual_sel = page.locator(f'select[name="{name}"], select[id="{name}"]')
    if actual_sel.count() > 0:
        fill_select(actual_sel.first, value)
        return

    # 2. Radio buttons ----------------------------------------------------
    radio_name = page.locator(f'input[type="radio"][name="{name}"]')
    radio_id = page.locator(f'input[type="radio"][id*="{name}"]')
    radio_group = radio_name if radio_name.count() > 0 else radio_id
    if radio_group.count() > 0:
        fill_radio(page, radio_group, name, value)
        return

    # 3. Checkbox ---------------------------------------------------------
    cb_name = page.locator(f'input[type="checkbox"][name="{name}"]')
    cb_id = page.locator(f'input[type="checkbox"][id="{name}"]')
    cb_loc = cb_name if cb_name.count() > 0 else cb_id
    if cb_loc.count() > 0:
        # Multi-option checkbox GROUP (e.g. name="question_X[]" for a
        # 50-state grid). When >1 checkbox shares the same name, we
        # must pick the ones whose value attr OR associated <label for>
        # text matches ``value`` — NOT just ``.first``, which always
        # hits Alabama. Regression: mthree job 848 Jr. Java Developer,
        # where the LLM answered "Maryland" but Alabama got checked.
        if cb_loc.count() > 1:
            # Support comma-separated values for multi-select.
            wanted = {
                v.strip().lower()
                for v in value.split(",")
                if v.strip()
            }
            any_checked = False
            for i in range(cb_loc.count()):
                try:
                    cb = cb_loc.nth(i)
                    cb_val = (cb.get_attribute("value") or "").strip().lower()
                    label_text = ""
                    cb_eid = cb.get_attribute("id") or ""
                    if cb_eid:
                        lbl = page.locator(f'label[for="{cb_eid}"]')
                        if lbl.count() > 0:
                            label_text = (
                                lbl.first.inner_text() or ""
                            ).strip().lower()
                    if cb_val in wanted or label_text in wanted:
                        cb.check(timeout=3_000, force=True)
                        any_checked = True
                except Exception as exc:  # noqa: BLE001
                    log.debug(
                        "fill_field: checkbox group %r iter %d failed: %s",
                        name, i, exc,
                    )
            if not any_checked:
                log.debug(
                    "fill_field: no checkbox in group %r matches value %r",
                    name, value,
                )
            return

        truthy = value.strip().lower() in ("yes", "true", "1", "on")
        if truthy:
            cb_loc.first.check(timeout=3_000)
        else:
            cb_loc.first.uncheck(timeout=3_000)
        return

    # 4. Text / textarea / number / email / tel --------------------------
    # Try [name=], then [id=], then [id="name[]"], then case-insensitive
    # name. Some Greenhouse checkboxes append [] to the id:
    # id="question_123[]". Lever's URL fields use camelCase, so we also
    # try a case-insensitive CSS attribute selector (CSS4 "i" flag) to
    # catch `urls[LinkedIn]` vs `urls[linkedin]`.
    for selector in (
        f'[name="{name}"]',
        f'[id="{name}"]',
        f'[id="{name}[]"]',
        f'[name="{name}" i]',
    ):
        try:
            loc = page.locator(selector)
            if loc.count() > 0:
                el = loc.first
                # React-Select detection — the new job-boards.greenhouse.io
                # SPA renders EVERY question (including simple Yes/No) as a
                # React-Select combobox. Detect via role, ancestor class,
                # or a post-fill verification.
                if _is_react_select(el):
                    fill_combobox(page, el, value)
                    return
                # Plain text/textarea/email/tel — try .fill() first, then
                # verify the value stuck. React-Select widgets whose inputs
                # don't have role="combobox" or a detectable ancestor class
                # ignore direct .fill() and leave the value empty; in that
                # case, fall back to the combobox flow.
                try:
                    el.fill(value, timeout=5_000)
                    actual = (el.input_value(timeout=500) or "").strip()
                    if not actual:
                        log.debug(
                            "fill_field: .fill() didn't stick for name=%r — "
                            "retrying as combobox", name,
                        )
                        fill_combobox(page, el, value)
                except Exception:
                    # .fill() / input_value() can raise on non-standard
                    # widgets. Fall back to combobox flow.
                    fill_combobox(page, el, value)
                return
        except Exception:
            continue

    # Nothing found — caller (phases.api_fill) interprets this as
    # "field absent on this tenant's DOM" (debug-log, not field error).
    raise ValueError(f"no element found for name/id={name!r}")
