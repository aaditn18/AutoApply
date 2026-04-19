"""Label-aware fallback for unresolved SPA-injected form fields.

The new ``job-boards.greenhouse.io`` React SPA injects tenant-specific fields
that are NOT in Greenhouse's API ``/questions`` endpoint — things like
``Location (City)*``, ``What city do you currently reside in?*``, and per-tenant
demographic inputs. Our main :func:`fill_field` loop keys fields by ``name`` /
``id``, so it can't fill these unless the resolver pre-populates ``data`` with
the right attribute names.

This module does the reverse: after the main fill loop runs, it walks the
DOM for *empty required* inputs / selects / textareas, reads each one's
visible label, runs the label through :func:`classify`, and — when the
resulting :class:`QuestionType` maps to a value we know — fills it.

This is deliberately narrow:
  * Only empty fields are touched (we never clobber a pre-filled value).
  * Only classifier rule hits ``confidence=1.0`` are honored; UNKNOWN is
    skipped so we never dump a random answer into a policy question.
  * The caller supplies which QuestionType values are "safe to autofill
    from DOM labels" via the ``label_values`` dict.

Keeping the set explicit (rather than "fill anything we can classify")
preserves the review-queue invariant: demographic / policy fields still
route through the bank + review path, and only the atomic location
fields come through here.
"""

from __future__ import annotations

import logging
from typing import Any

from autoapply.answers.classifier import classify
from autoapply.answers.types import QuestionType

from .field_fill import _is_react_select, fill_combobox, fill_select
from .util import jitter


log = logging.getLogger(__name__)


# Text-input types we're willing to fill via label match. Skip file /
# hidden / submit / etc.
_TEXT_INPUT_TYPES = {"text", "email", "tel", "search", "url", "number", ""}


def fill_by_label(
    page: Any,
    label_values: dict[str, str],
    already_filled_keys: set[str],
    field_errors: list[str],
) -> None:
    """Iterate empty form fields and fill those whose label classifies.

    Args:
      page: live Playwright ``Page``.
      label_values: ``{QuestionType.value: value_string}`` — the only
        question types we are willing to autofill from DOM labels.
        Callers pass atomic location fields (``current_city`` etc.)
        here; demographic / policy fields are intentionally omitted.
      already_filled_keys: ``name``/``id`` attributes the main fill
        loop already handled. Skip these to avoid reclassifying and
        overwriting a field we just set.
      field_errors: appended with ``"label_fallback:{tag}:{exc}"`` on
        per-field failures (non-fatal).
    """
    if not label_values:
        return

    # Gather candidate inputs, selects, and textareas.
    candidates: list[Any] = []
    try:
        candidates.extend(page.locator("input:not([type=hidden])").all())
        candidates.extend(page.locator("select").all())
        candidates.extend(page.locator("textarea").all())
    except Exception as exc:
        log.debug("label_fallback: could not enumerate form controls: %s", exc)
        return

    seen_ids: set[str] = set()

    for el in candidates:
        try:
            # De-dup on element id (same element can appear under several
            # CSS queries).
            el_id = el.get_attribute("id") or ""
            el_name = el.get_attribute("name") or ""
            dedup_key = el_id or el_name
            if dedup_key and dedup_key in seen_ids:
                continue
            if dedup_key:
                seen_ids.add(dedup_key)

            # Skip things the main loop already touched (same name or id
            # as one of the data keys we filled).
            if el_name in already_filled_keys or el_id in already_filled_keys:
                continue

            # Skip inputs whose type we don't handle.
            input_type = (el.get_attribute("type") or "").lower()
            tag_name = _tag_name(el)
            if tag_name == "input" and input_type not in _TEXT_INPUT_TYPES:
                continue

            # Skip non-empty fields — preserves values set by earlier passes.
            if _has_value(el, tag_name):
                continue

            # Skip fields that aren't visible on screen (e.g. collapsed
            # sections the user didn't expand). Avoids races where we'd
            # click an offscreen combobox and open a phantom dropdown.
            try:
                if not el.is_visible(timeout=500):
                    continue
            except Exception:
                continue

            label_text = _label_for(page, el)
            if not label_text:
                continue

            classified = classify(label_text)
            if classified.confidence < 1.0:
                continue

            target_value = label_values.get(classified.type.value)
            if not target_value:
                continue

            # Dispatch by element kind.
            _apply_value(page, el, tag_name, target_value, classified.type)
            log.info(
                "label_fallback: filled %s=%r via label=%r (type=%s)",
                dedup_key or tag_name,
                target_value,
                label_text[:60],
                classified.type.value,
            )
            jitter(0.1, 0.3)

        except Exception as exc:
            # Per-field failures are informational — the submit may still
            # succeed without this field, and if it's required the
            # visible-errors detector will surface the real blocker.
            log.debug("label_fallback: skipped field due to %s: %s", type(exc).__name__, exc)
            field_errors.append(f"label_fallback:{type(exc).__name__}")


# --- internals -------------------------------------------------------------


def _tag_name(el: Any) -> str:
    try:
        return (
            el.evaluate("e => e.tagName && e.tagName.toLowerCase()") or ""
        )
    except Exception:
        return ""


def _has_value(el: Any, tag_name: str) -> bool:
    """True if the element already has a non-empty value.

    For ``<select>`` we treat ``"","Select..."`` placeholder as empty so the
    fallback can pick a real option. For text/textarea we check ``.value``.
    """
    try:
        if tag_name == "select":
            raw = el.evaluate("e => e.value") or ""
            return bool(raw) and raw.lower() not in ("", "select...", "select")
        raw = el.evaluate("e => e.value") or ""
        return bool(raw.strip())
    except Exception:
        return False


def _label_for(page: Any, el: Any) -> str:
    """Best-effort label lookup for a form element.

    Priority:
      1. ``aria-label`` attribute.
      2. ``aria-labelledby`` → referenced element's text.
      3. ``<label for="id">`` sibling.
      4. Ancestor ``<label>`` element.
      5. ``placeholder`` attribute.
    """
    # aria-label
    try:
        al = (el.get_attribute("aria-label") or "").strip()
        if al:
            return al
    except Exception:
        pass

    # aria-labelledby → element text
    try:
        alb = (el.get_attribute("aria-labelledby") or "").strip()
        if alb:
            # Can reference multiple IDs — concatenate their text.
            parts: list[str] = []
            for ref_id in alb.split():
                loc = page.locator(f'[id="{ref_id}"]')
                if loc.count() > 0:
                    try:
                        parts.append(loc.first.inner_text().strip())
                    except Exception:
                        pass
            joined = " ".join(p for p in parts if p)
            if joined:
                return joined
    except Exception:
        pass

    # <label for="id">
    try:
        el_id = el.get_attribute("id") or ""
        if el_id:
            # Use attribute selector (the id may contain CSS-special chars
            # like `[]` — common in Greenhouse array inputs).
            lbl = page.locator(f'label[for="{el_id}"]')
            if lbl.count() > 0:
                txt = lbl.first.inner_text().strip()
                if txt:
                    return txt
    except Exception:
        pass

    # Ancestor <label>
    try:
        anc = el.locator("xpath=ancestor::label[1]")
        if anc.count() > 0:
            txt = anc.first.inner_text().strip()
            if txt:
                return txt
    except Exception:
        pass

    # placeholder — low-quality but sometimes the only hint
    try:
        ph = (el.get_attribute("placeholder") or "").strip()
        if ph:
            return ph
    except Exception:
        pass

    return ""


def _apply_value(
    page: Any,
    el: Any,
    tag_name: str,
    value: str,
    qt: QuestionType,
) -> None:
    """Dispatch a fill based on tag / role.

    Mirrors the logic in :func:`autoapply.execute.submitter.field_fill.fill_field`:
      * native ``<select>``           → :func:`fill_select`
      * React-Select combobox wrapper → :func:`fill_combobox`
      * plain text / textarea         → ``.fill()`` with verify-and-retry
    """
    if tag_name == "select":
        fill_select(el, value)
        return
    if _is_react_select(el):
        fill_combobox(page, el, value)
        return
    # Plain text — try .fill() then verify; fall back to combobox on empty
    # (same pattern as fill_field for the main loop).
    try:
        el.fill(value, timeout=3_000)
        actual = (el.input_value(timeout=500) or "").strip()
        if not actual:
            fill_combobox(page, el, value)
    except Exception:
        fill_combobox(page, el, value)
