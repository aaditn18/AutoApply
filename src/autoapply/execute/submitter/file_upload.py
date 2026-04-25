"""File input handling — upload_file + file_input_selector.

Prefers the browser's native file-chooser API so that React upload widgets
(Lever, some Greenhouse tenants) receive the events their state machines
listen for. Plain ``set_input_files()`` bypasses those handlers and causes
spurious "file too large" / "invalid file" errors.

For React drop-zone widgets (Ashby) that listen for native ``drop``
events instead of input ``change``, ``set_input_files`` returns
silently but the widget never registers the file. ``upload_file``
follows up with a synthetic drop-event dispatch when the URL host
matches a known drop-zone tenant (currently Ashby).
"""

from __future__ import annotations

import base64
import logging
import mimetypes
from pathlib import Path
from typing import Any


log = logging.getLogger(__name__)


def upload_file(page: Any, selector: str, abs_path: str) -> None:
    """Upload a file to a file input, preferring the file-chooser API.

    The file-chooser API (``expect_file_chooser → click → set_files``) fires
    all browser-native events including the ones that React upload widgets
    (Lever, some Greenhouse tenants) listen to for state management.
    A plain ``set_input_files()`` call bypasses these handlers, which causes
    React to show spurious "file too large" or "invalid file" errors.

    Falls back to ``set_input_files()`` if the chooser dialog does not open
    within 3 s (e.g., the element is hidden / non-interactive).

    For Ashby's hosted apply pages, follows up with a synthetic
    ``drop`` event dispatched on the input's nearest drop-zone
    container. Ashby's React widget listens for ``drop`` to register
    the attach, NOT the standard ``change`` event — without this,
    ``set_input_files`` succeeds at the Playwright level but the
    form keeps showing "Upload your resume here…" placeholder.
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
    except Exception:
        # Fallback: set_input_files directly. Works for standard
        # <input type=file>; Ashby's drop-zone-wrapped input also
        # accepts it but the widget doesn't register attachment.
        try:
            page.set_input_files(selector, abs_path, timeout=8_000)
        except Exception as exc:
            log.debug("set_input_files failed for %r: %s", selector, exc)

    # Belt-and-suspenders for React drop-zones (Ashby). The drop
    # event dispatch is a no-op on a real <input type=file> with a
    # listener already registered by the chooser path — re-firing
    # change with the same files object is idempotent. On Ashby it
    # is what actually populates the visual "Resume.pdf attached"
    # state and triggers their server-side autofill.
    try:
        if "ashbyhq.com" in (getattr(page, "url", "") or ""):
            _dispatch_drop_event(page, selector, abs_path)
    except Exception as exc:
        log.debug("drop-event dispatch raised for %r: %s", selector, exc)


def _dispatch_drop_event(page: Any, selector: str, abs_path: str) -> bool:
    """Dispatch a synthetic ``drop`` on the input's drop-zone container.

    Reads the file, base64-encodes it, builds a JS ``File`` + ``DataTransfer``
    in-page, then fires the ``dragenter`` / ``dragover`` / ``drop`` sequence
    on the closest drop-zone ancestor of the input. Most React upload
    libraries (react-dropzone, Ashby's custom) listen for these events to
    set their internal state and start the autofill flow.

    Also re-fires ``change`` on the input with the same ``DataTransfer.files``
    so non-drop-zone widgets get a consistent signal.

    Returns ``True`` when the JS evaluate completed without error
    (best-effort — we don't have a reliable way to confirm React's
    state actually updated). Failures are logged at DEBUG.
    """
    p = Path(abs_path)
    if not p.exists():
        log.debug("drop-event: file missing %s", abs_path)
        return False
    try:
        content = p.read_bytes()
    except Exception as exc:
        log.debug("drop-event: read failed %s: %s", abs_path, exc)
        return False

    b64 = base64.b64encode(content).decode("ascii")
    mime = (mimetypes.guess_type(p.name)[0]
            or ("application/pdf" if p.suffix.lower() == ".pdf" else "application/octet-stream"))

    js = """
        async ({selector, b64, fileName, mimeType}) => {
            const input = document.querySelector(selector);
            if (!input) return {ok: false, reason: 'no-input'};

            // Decode base64 → Uint8Array → File (browsers refuse to
            // construct File from a Node Buffer; this is the only
            // portable path).
            const binary = atob(b64);
            const bytes = new Uint8Array(binary.length);
            for (let i = 0; i < binary.length; i++) {
                bytes[i] = binary.charCodeAt(i);
            }
            const file = new File([bytes], fileName, {type: mimeType});

            // Find the closest drop-zone — class containing "drop",
            // role=presentation, or just the parent. Ashby uses a
            // wrapper div with class containing "_dropzone_" or
            // similar Emotion-hashed name, so the substring match
            // catches it.
            let dropZone = input.closest(
                '[class*="drop"], [class*="Drop"], [class*="upload"], '
                + '[class*="Upload"], [data-rfid="drop"], [role="presentation"]'
            );
            if (!dropZone) dropZone = input.parentElement;
            if (!dropZone) return {ok: false, reason: 'no-zone'};

            const dt = new DataTransfer();
            dt.items.add(file);

            // Many drop-zones gate `drop` on a preceding `dragenter`
            // / `dragover`. Fire the full sequence.
            for (const evtName of ['dragenter', 'dragover', 'drop']) {
                dropZone.dispatchEvent(new DragEvent(evtName, {
                    bubbles: true,
                    cancelable: true,
                    dataTransfer: dt,
                }));
            }

            // Also set the input's `files` so any old-school listener
            // sees the same payload, and re-fire `change`.
            try {
                Object.defineProperty(input, 'files', {
                    value: dt.files, writable: false, configurable: true,
                });
            } catch (e) { /* readonly already → ignore */ }
            input.dispatchEvent(new Event('input', {bubbles: true}));
            input.dispatchEvent(new Event('change', {bubbles: true}));

            return {ok: true, dropZoneClass: dropZone.className || ''};
        }
    """
    try:
        result = page.evaluate(
            js,
            {
                "selector": selector,
                "b64": b64,
                "fileName": p.name,
                "mimeType": mime,
            },
        )
    except Exception as exc:
        log.debug("drop-event: evaluate raised — %s", exc)
        return False

    ok = bool(isinstance(result, dict) and result.get("ok"))
    if ok:
        log.info(
            "drop-event upload dispatched for %s (zone=%s)",
            p.name, (result.get("dropZoneClass") or "")[:60],
        )
    else:
        log.debug("drop-event: not dispatched — %r", result)
    return ok


def file_input_selector(page: Any, name: str) -> str | None:
    """Return a CSS selector that locates the file ``<input>`` for the given field name.

    Tries ``name=`` attribute first, then ``id=`` attribute (new Greenhouse
    job-boards SPA omits name attributes). Returns ``None`` if nothing is found.

    Fallback (for Ashby's hosted SPA and similar opaque-DOM tenants): when
    ``name`` is ``"resume"`` or ``"cover_letter"`` and the specific-attribute
    candidates miss, locate ``input[type="file"]`` elements by proximity to
    label text (e.g. a ``<label>`` with "Resume", "CV", or "Upload resume").
    """
    candidates = [
        f'input[type="file"][name="{name}"]',
        f'input[type="file"][id="{name}"]',
        f'input[type="file"]#{name}',
        # Substring-match fallback — covers tenants that prefix the
        # attribute (Ashby uses ``_systemfield_resume`` for the resume
        # input). CSS4 ``i`` flag makes it case-insensitive.
        f'input[type="file"][name*="{name}" i]',
        f'input[type="file"][id*="{name}" i]',
    ]
    for sel in candidates:
        try:
            if page.locator(sel).count() > 0:
                return sel
        except Exception:
            pass

    # Proximity fallback for resume + cover letter — Ashby (and other
    # SPA tenants) omit stable name/id attributes.
    if name in ("resume", "cover_letter"):
        phrases = {
            "resume": ("resume", "cv", "upload resume"),
            "cover_letter": ("cover letter", "cover_letter"),
        }[name]
        try:
            matched_id = page.evaluate(
                """(phrases) => {
                    const inputs = Array.from(
                        document.querySelectorAll('input[type="file"]')
                    );
                    const hit = (txt) => phrases.some(p => (txt || '').toLowerCase().includes(p));
                    for (const inp of inputs) {
                        // 1. Associated <label for=...>
                        const id = inp.id;
                        if (id) {
                            const lbl = document.querySelector(
                                `label[for="${CSS.escape(id)}"]`
                            );
                            if (lbl && hit(lbl.innerText)) return id;
                        }
                        // 2. Ancestor <label>
                        const anc = inp.closest('label');
                        if (anc && hit(anc.innerText)) return id || anc.id || null;
                        // 3. Container text (up to 4 levels up)
                        let container = inp.parentElement;
                        for (let i = 0; i < 4 && container; i++) {
                            if (hit(container.innerText)) return id || null;
                            container = container.parentElement;
                        }
                    }
                    return null;
                }""",
                list(phrases),
            )
            if matched_id:
                return f'input[type="file"][id="{matched_id}"]'
            # Last resort: if only one file input exists on the page,
            # assume that's the resume slot.
            if name == "resume":
                inputs = page.locator('input[type="file"]')
                if inputs.count() == 1:
                    return 'input[type="file"]'
        except Exception:
            pass
    return None
