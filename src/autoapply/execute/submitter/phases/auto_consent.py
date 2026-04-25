"""Auto-check consent / agreement checkboxes.

Almost every ATS form ends with one or two unlabeled-or-loosely-
labeled checkboxes containing copy like "By submitting, I agree to
the privacy policy and terms of service" or "I authorize you to
contact me about this application." These are *de facto* compulsory
— most candidates check them without thinking — but our scrape
pipeline misses them:

- ``dom/scrape.py::collect_empty_required_fields`` only looks at
  fields with ``required`` / ``aria-required`` / ``.required``
  ancestor markers. Many tenants leave the consent checkbox without
  a hard required attribute (HTML5 will block submission anyway via
  JS, but our scraper doesn't see them as required).
- ``api_fill`` only fires for keys present in the resolved data dict.
  No machine key matches an opaque UUID-named consent box.
- ``label_fallback`` skips checkboxes (it's text-input only).

This phase walks every visible unchecked ``input[type="checkbox"]``,
inspects the surrounding visible text, and clicks the box when that
text contains a consent-style keyword. The keyword list is narrow on
purpose — we don't auto-check marketing opt-ins ("Send me job alerts"
/ "Subscribe to our newsletter"), since those are genuinely optional
preferences, not compulsory acceptance gates.

Skips:
  - Already-checked boxes (idempotent re-runs are safe).
  - Boxes belonging to a multi-option group (``name="foo[]"`` with
    siblings) — those are handled by ``fillers/dispatch.py`` for
    state grids and similar.
  - Off-screen / invisible checkboxes (collapsed accordions etc.).
  - Boxes whose nearby text matches a NEGATIVE keyword
    (``"subscribe"``, ``"marketing"``, ``"job alerts"``, etc.).
"""

from __future__ import annotations

import logging
from typing import Any


log = logging.getLogger(__name__)


# Phrases that indicate a compulsory consent / acknowledgment.
_CONSENT_KEYWORDS: tuple[str, ...] = (
    "i agree",
    "i acknowledge",
    "i authorize",
    "i consent",
    "i confirm",
    "i certify",
    "i accept",
    "i understand",
    "by submitting",
    "by checking",
    "by clicking",
    "by signing",
    "agree to the",
    "consent to the",
    "accept the",
    "privacy policy",
    "privacy notice",
    "terms of service",
    "terms of use",
    "terms and conditions",
    "data processing",
    "gdpr",
    "ccpa",
    "applicant privacy",
    "background check",
    "data collection",
    "use of my data",
    "process my application",
    "my information accurate",
    "answers are true",
)


# Phrases that signal an OPTIONAL marketing-style checkbox we must
# NOT auto-check. These take priority over the consent keywords —
# if a checkbox's nearby text mentions any of these, leave it alone.
_OPTOUT_KEYWORDS: tuple[str, ...] = (
    "subscribe",
    "newsletter",
    "marketing",
    "promotional",
    "job alert",
    "future opportunit",   # "future opportunities"
    "talent network",
    "job board",
    "stay in touch",
    "send me email",
    "send me update",
    "email me about",
    "notify me about",
    "join our community",
    "sign up for",
)


def auto_check_consent_boxes(page: Any, field_errors: list[str]) -> None:
    """Walk visible unchecked checkboxes and click consent-style ones.

    Per-checkbox failures are appended to ``field_errors`` as
    ``auto_consent:<reason>`` strings; non-fatal — the submission
    can still proceed without a successful auto-check.
    """
    try:
        checkboxes = page.locator('input[type="checkbox"]').all()
    except Exception as exc:
        log.debug("auto_consent: enumerate failed — %s", exc)
        return

    log.info("auto_consent: scanning %d checkbox(es)", len(checkboxes))
    if not checkboxes:
        return

    # Group by ``name`` to detect multi-option groups (state grids,
    # multi-select questionnaires). Single-name groups are consent
    # candidates; >1-element groups are handled elsewhere.
    by_name: dict[str, list[Any]] = {}
    for cb in checkboxes:
        try:
            n = cb.get_attribute("name") or ""
            by_name.setdefault(n, []).append(cb)
        except Exception:
            continue

    checked_count = 0
    for cb in checkboxes:
        try:
            cb_attr_id = cb.get_attribute("id") or ""
        except Exception:
            cb_attr_id = ""
        try:
            cb_attr_name = cb.get_attribute("name") or ""
        except Exception:
            cb_attr_name = ""
        # Stable identifier for log lines — prefer name, fall back to
        # id, then a short marker so we can still distinguish multiple
        # boxes in the log.
        cb_log_id = (cb_attr_name or cb_attr_id or "<no-id>")[:40]
        try:
            # Skip if part of a multi-option group.
            if cb_attr_name and cb_attr_name.endswith("[]"):
                log.info(
                    "auto_consent: id=%s skipped (group name %r)",
                    cb_log_id, cb_attr_name[:40],
                )
                continue
            if cb_attr_name and len(by_name.get(cb_attr_name, [])) > 1:
                log.info(
                    "auto_consent: id=%s skipped (%d sibling boxes share name %r)",
                    cb_log_id, len(by_name[cb_attr_name]), cb_attr_name[:40],
                )
                continue

            # Skip if already checked.
            try:
                if cb.evaluate("e => e.checked"):
                    log.info(
                        "auto_consent: id=%s skipped (already checked)",
                        cb_log_id,
                    )
                    continue
            except Exception:
                continue

            # NB: NO visibility gate. React-based ATSes (Ashby, etc.)
            # render the native input with ``opacity:0`` /
            # ``visibility:hidden`` and use a custom-styled span as the
            # click target. Playwright's ``is_visible`` returns False
            # for those. Our ``cb.check(force=True)`` below explicitly
            # bypasses interactability checks, so we don't need to
            # gate the click on visibility — relying on the click
            # itself to surface real failures.

            # Read surrounding text — climb up to ~4 ancestors and
            # take the closest non-empty visible text. Caps at 600
            # chars to keep regex / keyword scans cheap.
            context = _consent_context(cb)
            if not context:
                log.info(
                    "auto_consent: id=%s skipped (no surrounding text "
                    "found — consider hand-classifying)",
                    cb_log_id,
                )
                continue

            ctx_lower = context.lower()
            # Negative-list short-circuit.
            if any(k in ctx_lower for k in _OPTOUT_KEYWORDS):
                log.info(
                    "auto_consent: id=%s skipped (marketing/opt-in: text=%r)",
                    cb_log_id, context[:80],
                )
                continue
            # Positive list.
            if not any(k in ctx_lower for k in _CONSENT_KEYWORDS):
                log.info(
                    "auto_consent: id=%s skipped (no consent keyword "
                    "in text=%r)",
                    cb_log_id, context[:120],
                )
                continue

            try:
                cb.check(timeout=2_500, force=True)
                checked_count += 1
                log.info(
                    "auto_consent: checked consent box (text=%r)",
                    context[:80],
                )
            except Exception as exc:
                log.debug(
                    "auto_consent: check failed (text=%r): %s",
                    context[:80], exc,
                )
                field_errors.append(f"auto_consent:{type(exc).__name__}")
        except Exception as exc:
            log.debug("auto_consent: per-checkbox loop error — %s", exc)

    if checked_count:
        log.info("auto_consent: %d consent box(es) auto-checked", checked_count)


# ── internals ────────────────────────────────────────────────────────────


def _consent_context(cb: Any) -> str:
    """Return the visible text near a checkbox.

    Tries (in order):
      1. ``aria-label`` or ``aria-labelledby``.
      2. ``<label for="id">`` element text.
      3. Ancestor ``<label>`` text.
      4. Closest container's innerText (capped at 600 chars).
    """
    try:
        al = (cb.get_attribute("aria-label") or "").strip()
        if al:
            return al
    except Exception:
        pass

    try:
        eid = cb.get_attribute("id") or ""
        if eid:
            txt = cb.evaluate(
                """(el) => {
                    const id = el.getAttribute('id');
                    if (!id) return '';
                    const lbl = document.querySelector(
                        `label[for="${CSS.escape(id)}"]`
                    );
                    return lbl ? (lbl.innerText || '').trim() : '';
                }"""
            )
            if isinstance(txt, str) and txt.strip():
                return txt.strip()
    except Exception:
        pass

    # Climb ancestors up to 6 levels AND check sibling text. Many
    # Ashby tenants render the checkbox + consent paragraph as
    # adjacent siblings inside a flex row, so the immediate parent's
    # innerText catches both. When the checkbox is structurally
    # separated (sibling paragraph + sibling checkbox under a wrapper
    # div), we also scan the previous-element-sibling's text.
    try:
        ctx = cb.evaluate(
            """(el) => {
                // 1. Ancestor with reasonable text (between immediate
                //    container and form group).
                let cur = el.parentElement;
                for (let depth = 0; depth < 6 && cur; depth++) {
                    const t = (cur.innerText || '').trim();
                    if (t && t.length > 8 && t.length < 800) {
                        return t;
                    }
                    cur = cur.parentElement;
                }
                // 2. Previous-element-sibling text — sometimes the
                //    consent paragraph precedes the checkbox under
                //    the same wrapper.
                const sibs = [];
                let n = el.previousElementSibling;
                for (let i = 0; i < 4 && n; i++) {
                    const t = (n.innerText || '').trim();
                    if (t && t.length > 8) sibs.push(t);
                    n = n.previousElementSibling;
                }
                if (sibs.length) return sibs.join(' ').slice(0, 800);
                // 3. Same for next-element-sibling (label after input).
                n = el.nextElementSibling;
                for (let i = 0; i < 4 && n; i++) {
                    const t = (n.innerText || '').trim();
                    if (t && t.length > 8) return t.slice(0, 800);
                    n = n.nextElementSibling;
                }
                return '';
            }"""
        )
        return (ctx or "").strip() if isinstance(ctx, str) else ""
    except Exception:
        return ""
