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
    """Log every ``[name]`` field's current value + any unfilled required fields.

    Lever-scoped — called only from the driver when the URL contains
    ``jobs.lever.co``. Used to verify that our text fills stuck through
    the React re-render and that every required field has a value before
    the submit click.

    Runs the check in the browser via a single ``page.evaluate`` so we
    don't make hundreds of round-trips. Radio-group de-duping: if the
    "Yes" radio in a group is checked, the unchecked "No" radio should
    not be flagged as unfilled.
    """
    try:
        state = page.evaluate(
            """() => {
                const rows = [];
                const unfilled_required = [];
                const radio_group_checked = {};  // name → any-checked?
                // Pass 1: determine if each radio-group name has
                // any checked sibling so we don't flag the
                // unchecked radios as "unfilled required".
                document.querySelectorAll('input[type="radio"][name]').forEach(el => {
                    const n = el.getAttribute('name');
                    if (!n) return;
                    if (el.checked) radio_group_checked[n] = true;
                });

                document.querySelectorAll('[name]').forEach(el => {
                    const n = el.getAttribute('name');
                    if (!n) return;
                    const t = (el.getAttribute('type') || el.tagName).toLowerCase();
                    if (t === 'hidden') return;
                    let v = '';
                    if (t === 'checkbox' || t === 'radio') {
                        v = el.checked ? (el.value || 'on') : '';
                    } else {
                        v = el.value || '';
                    }
                    rows.push({name: n, type: t, value: (v || '').slice(0, 60)});
                    // Required + empty → flag it, but skip
                    // unchecked radios whose group has a checked sibling.
                    const req = el.required
                        || el.getAttribute('aria-required') === 'true'
                        || el.closest('.application-question')?.querySelector('.required');
                    const skip_radio = (t === 'radio' && radio_group_checked[n]);
                    if (req && !v && !skip_radio && t !== 'file' && t !== 'submit') {
                        unfilled_required.push({name: n, type: t});
                    }
                });
                return {rows, unfilled_required};
            }"""
        )
        for row in state.get("rows", [])[:40]:
            log.info(
                "pre-submit field %-40s type=%-10s value=%r",
                row["name"][:40], row["type"], row["value"],
            )
        if state.get("unfilled_required"):
            log.warning(
                "pre-submit UNFILLED REQUIRED fields (%d): %s",
                len(state["unfilled_required"]),
                [f["name"] for f in state["unfilled_required"]][:20],
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
