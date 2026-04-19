"""Pre- and post-submit diagnostic dumps — Lever-scoped.

Extracted from the inline blocks that used to live in ``_submit_form``.
Helps us reason about *why* a Lever submit failed (e.g., hCaptcha backend
rejected, unfilled required field, server-side rate limit) without needing
to stand up a headful browser.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any


log = logging.getLogger(__name__)


def dump_pre_submit_state(page: Any) -> None:
    """Log every form field's current value + any unfilled required fields.

    Called just before the submit click on both Lever and Greenhouse. Used
    to verify that our text fills stuck through any React re-render and
    that every required field has a value before submit. For unfilled
    required fields we also dump the associated label and — for dropdowns
    / comboboxes — the list of available options, so we can see WHY the
    fill didn't match (option text mismatch is the usual cause).

    Runs the whole check in the browser via a single ``page.evaluate`` so
    we don't make hundreds of round-trips. Radio-group de-duping: if the
    "Yes" radio in a group is checked, the unchecked "No" radio should
    not be flagged as unfilled.

    Union-keyed on ``[name]`` ∪ ``[id]`` so we pick up the new
    ``job-boards.greenhouse.io`` SPA fields (which use ``id`` only,
    no ``name`` attribute).
    """
    try:
        state = page.evaluate(
            """() => {
                const rows = [];
                const unfilled_required = [];
                const radio_group_checked = {};
                document.querySelectorAll('input[type="radio"]').forEach(el => {
                    const n = el.getAttribute('name') || el.getAttribute('id');
                    if (!n) return;
                    if (el.checked) radio_group_checked[n] = true;
                });

                // Label lookup: aria-label → aria-labelledby → <label for=id>
                // → ancestor <label> → placeholder. Kept concise to avoid
                // blowing up the payload size.
                const labelOf = (el) => {
                    const al = (el.getAttribute('aria-label') || '').trim();
                    if (al) return al;
                    const alb = el.getAttribute('aria-labelledby');
                    if (alb) {
                        const node = document.getElementById(alb.split(' ')[0]);
                        if (node) return (node.innerText || '').trim().slice(0, 120);
                    }
                    const id = el.getAttribute('id');
                    if (id) {
                        const lbl = document.querySelector(`label[for="${CSS.escape(id)}"]`);
                        if (lbl) return (lbl.innerText || '').trim().slice(0, 120);
                    }
                    const anc = el.closest('label');
                    if (anc) return (anc.innerText || '').trim().slice(0, 120);
                    return (el.getAttribute('placeholder') || '').trim();
                };

                // Collect real <select> option text for visibility into
                // why a fill may have failed ("our answer wasn't an option").
                const optionsOf = (el) => {
                    if (el.tagName !== 'SELECT') return null;
                    return Array.from(el.querySelectorAll('option'))
                        .map(o => (o.textContent || '').trim())
                        .filter(Boolean).slice(0, 20);
                };

                // React-Select: the input has role="combobox" and the
                // selected-value text is rendered in a sibling element
                // with varying class names depending on React-Select
                // version:
                //   v5:  .select__single-value / .select__multi-value
                //   v4:  .css-XXXX-singleValue (Emotion-hashed)
                //   v3:  .Select__single-value
                // We try several class substrings AND fall back to the
                // wrapper's .innerText minus placeholder / indicator text.
                const comboValueOf = (el) => {
                    const wrapper = el.closest(
                        '[class*="container"], [class*="Select"], '
                        + '[class*="-control"]'
                    );
                    if (!wrapper) return null;
                    const sel = [
                        '[class*="singleValue"]',
                        '[class*="single-value"]',
                        '[class*="singleval"]',
                        '[class*="Value"] > div',
                    ].join(',');
                    const sv = wrapper.querySelector(sel);
                    if (sv) {
                        const txt = (sv.innerText || sv.textContent || '').trim();
                        if (txt) return txt;
                    }
                    // Fallback: read the whole control's text and strip
                    // placeholder-looking chunks.
                    const ctrl = wrapper.querySelector(
                        '[class*="control"]'
                    ) || wrapper;
                    const raw = (ctrl.innerText || '').trim();
                    if (/^select(\.{3})?$/i.test(raw)) return '';
                    return raw;
                };

                const seen = new Set();
                const sel = 'input:not([type="hidden"]):not([type="submit"]),select,textarea';
                document.querySelectorAll(sel).forEach(el => {
                    const n = el.getAttribute('name') || el.getAttribute('id') || '';
                    if (!n || seen.has(n)) return;
                    seen.add(n);
                    const t = (el.getAttribute('type') || el.tagName).toLowerCase();
                    if (t === 'hidden') return;
                    let v = '';
                    if (t === 'checkbox' || t === 'radio') {
                        v = el.checked ? (el.value || 'on') : '';
                    } else if (el.tagName === 'SELECT') {
                        v = el.value || '';
                        const opt = el.options[el.selectedIndex];
                        if (opt) v = opt.text || v;
                    } else {
                        v = el.value || comboValueOf(el) || '';
                    }
                    const row = {
                        name: n, type: t, value: (v || '').slice(0, 60),
                        label: labelOf(el).slice(0, 80),
                    };
                    const opts = optionsOf(el);
                    if (opts) row.options = opts;
                    rows.push(row);

                    const req = el.required
                        || el.getAttribute('aria-required') === 'true'
                        || el.closest('.application-question')?.querySelector('.required');
                    const skip_radio = (t === 'radio' && radio_group_checked[n]);
                    // A real select is "filled" only when a non-empty option
                    // with non-placeholder text is selected.
                    const placeholder_like = /^(select(\.{3})?|choose(\.{3})?|--|—|please\s+select)$/i
                        .test(v.trim());
                    // React-Select readback is unreliable across versions —
                    // the ``singleValue`` class name varies (Emotion hashes
                    // in v4, ``select__single-value`` in v5, etc.). We
                    // can't reliably tell "filled" from "empty" just from
                    // the DOM without opening the component. Skip the
                    // required-check for React-Select wrappers and rely
                    // on the server's post-submit ``visible_errors`` to
                    // tell us which fields are truly unfilled.
                    const is_react_select = !!(
                        el.getAttribute('role') === 'combobox'
                        || el.closest('[class*="select__control"], [class*="Select__control"], [class*="-control"]')
                    );
                    const is_empty_value = !v || placeholder_like;
                    if (req && is_empty_value && !skip_radio
                        && !is_react_select
                        && t !== 'file' && t !== 'submit') {
                        unfilled_required.push({
                            name: n, type: t, label: labelOf(el).slice(0, 80),
                            options: opts ? opts.slice(0, 10) : null,
                        });
                    }
                });
                return {rows, unfilled_required};
            }"""
        )
        for row in state.get("rows", [])[:60]:
            extra = ""
            if row.get("options"):
                extra = f" opts={row['options'][:6]}"
            log.info(
                "pre-submit field %-40s type=%-10s value=%r label=%r%s",
                row["name"][:40], row["type"], row["value"],
                (row.get("label") or "")[:50], extra,
            )
        for u in state.get("unfilled_required", [])[:10]:
            log.warning(
                "pre-submit UNFILLED REQUIRED field=%r type=%s label=%r opts=%s",
                u["name"], u["type"], (u.get("label") or "")[:80],
                (u.get("options") or [])[:8],
            )
    except Exception as exc:
        log.debug("pre-submit diagnostic failed: %s", exc)


def dump_post_submit_failure(page: Any, url: str) -> None:
    """Save a full-page screenshot + probe visible errors / hCaptcha iframes.

    Called from the driver after :func:`success_detect.detect_submit_success`
    reports failure, scoped to Lever. Saves to ``state/failed_submits/``
    and logs the hCaptcha iframe list + any visible error node text.

    Not used for Greenhouse — Greenhouse failures are logged via the
    post-submit page text dump in the driver itself. Lever needs more
    instrumentation because its failures are silent (form resets).
    """
    # Screenshot
    try:
        shot_dir = Path("state") / "failed_submits"
        shot_dir.mkdir(parents=True, exist_ok=True)
        tag = hashlib.md5(url.encode()).hexdigest()[:10]
        shot_path = shot_dir / f"lever_{tag}.png"
        page.screenshot(path=str(shot_path), full_page=True)
        log.info("saved failure screenshot: %s", shot_path)
    except Exception as exc:
        log.debug("screenshot failed: %s", exc)

    # hCaptcha iframe + visible error probe
    try:
        hc_info = page.evaluate(
            """() => {
                const frames = Array.from(
                    document.querySelectorAll('iframe[src*="hcaptcha"]')
                ).map(f => f.src);
                const errorNodes = Array.from(
                    document.querySelectorAll('.error, .alert-error, [class*="error"]')
                )
                .filter(e => e.offsetParent !== null)  // visible only
                .map(e => (e.innerText || '').trim().slice(0, 200))
                .filter(Boolean);
                return {hcaptcha_frames: frames.slice(0, 6), errors: errorNodes.slice(0, 8)};
            }"""
        )
        if hc_info.get("hcaptcha_frames"):
            log.info(
                "hCaptcha iframes present after submit: %s",
                hc_info["hcaptcha_frames"],
            )
        if hc_info.get("errors"):
            log.warning(
                "visible post-submit errors: %s",
                hc_info["errors"],
            )
    except Exception as exc:
        log.debug("post-submit probe failed: %s", exc)
