"""hCaptcha image-grid solver via 2Captcha's JSON API v2 (GridTask).

2Captcha has dropped `method=hcaptcha` from their current API entirely
(verified against https://2captcha.com/api-docs on 2026-04-18 — hCaptcha
is not listed anywhere in the docs index). The correct replacement for
image-grid challenges like hCaptcha's "Please click each image containing
a motorcycle" is their **GridTask** — an image-classification task that
returns a list of **tile indices** (1-indexed, top-left first), not
pixel coordinates.

Docs: https://2captcha.com/api-docs/grid

Why GridTask is better than CoordinatesTask for hCaptcha:
  - Returns discrete tile indices (`click: [2, 5, 7]`) — unambiguous;
    no off-by-a-few-pixels misses on the image-cell borders.
  - Works with the standard 3x3 and 4x4 grids hCaptcha renders.
  - Cheaper per solve — 2Captcha's pricing favors classification tasks.

Flow per invocation:
  1. Find the challenge iframe on the page (largest visible `iframe[src*=hcaptcha]`).
  2. Extract the prompt text ("Please click each image containing ...").
  3. Locate the IMAGE GRID area inside the iframe (we trim the prompt
     header and the Verify footer). Detect rows/columns from cell count.
  4. Screenshot ONLY the grid area.
  5. POST to api.2captcha.com/createTask with `type: "GridTask"`,
     `body: <base64-image>`, `comment: <prompt>`, `rows`, `columns`.
  6. Poll /getTaskResult until `status: "ready"` → get `solution.click`
     = list of 1-based tile indices.
  7. Convert each tile index to (row, col) → pixel center → page-absolute
     (x, y). Click each with `page.mouse.click()`.
  8. Click the Verify/Next button inside the iframe to advance.
  9. If a new round appears (different prompt), loop back to step 2.
 10. Return True if the iframe closes; False otherwise.
"""

from __future__ import annotations

import base64
import logging
import random
import time
from typing import Any


log = logging.getLogger(__name__)


# hCaptcha ships multiple challenge TYPES with different required 2Captcha
# task types. Classify by the prompt wording:
#
#   Image Grid (3×3/4×4 cell selection)  → 2Captcha GridTask
#     "Please click each image containing a motorcycle"
#   Shape/Object Coordinates (click N points) → 2Captcha CoordinatesTask
#     "Please click on the TWO shapes that are identical"
#     "Please click on the object that appears only once"
#
# The phrase lists below are NARROW — they must never trigger on the idle
# "I am human" checkbox widget, which is NOT a puzzle.

_GRID_PROMPT_PHRASES: tuple[str, ...] = (
    "please click each image containing",
    "please click on all images",
    "please click each image",
    "select all images",
    "please select all",
    "click the images",
    "click each image",
    "click each picture",
)

_COORDS_PROMPT_PHRASES: tuple[str, ...] = (
    # Shape / object matching puzzles
    "click on the two shapes that are identical",
    "click on the shapes that are identical",
    "shapes that are identical",
    "click on the identical",
    "click the identical",
    "click on the same shape",
    "click on the same object",
    "click on the object that matches",
    "click on the matching",
    "click on the object that appears only once",
    "click on the unique",
    "click on the different",
    # Generic "click on the <thing>" forms. These are the last resort
    # since they risk matching grid prompts too — `_classify_challenge`
    # checks grid phrases FIRST to avoid collisions.
    "click on the ",
    "click on each ",
)

# Combined list — used by `_extract_prompt` to decide if a frame's inner
# text contains any kind of puzzle prompt.
_PROMPT_PHRASES: tuple[str, ...] = _GRID_PROMPT_PHRASES + _COORDS_PROMPT_PHRASES

# Advance-button text, in click-preference order.
_ADVANCE_BUTTONS: tuple[str, ...] = ("Verify", "Next", "Submit", "Skip")


def _classify_challenge(prompt: str) -> str:
    """Return 'grid', 'coords', or 'unknown' based on prompt wording.

    Grid phrases are checked FIRST: prompts like "click each image
    containing" include both "click on" (coords) and "images" (grid),
    so if we checked coords first we'd mis-classify every grid puzzle.
    """
    p = (prompt or "").lower()
    if any(x in p for x in _GRID_PROMPT_PHRASES):
        return "grid"
    if any(x in p for x in _COORDS_PROMPT_PHRASES):
        return "coords"
    return "unknown"


# Public alias for the old call site — implementation now uses GridTask.
def solve_hcaptcha_coords(
    page: Any,
    *,
    api_key: str,
    total_timeout: int = 300,
    max_rounds: int = 5,
) -> bool:
    """Backward-compatible alias for `solve_hcaptcha_grid`.

    The function previously used 2Captcha's Coordinates API via the legacy
    `/in.php` endpoint. That path is superseded by GridTask on the JSON v2
    API — callers don't need to change the function name they invoke.
    """
    return solve_hcaptcha_grid(
        page,
        api_key=api_key,
        total_timeout=total_timeout,
        max_rounds=max_rounds,
    )


def solve_hcaptcha_grid(
    page: Any,
    *,
    api_key: str,
    total_timeout: int = 300,
    max_rounds: int = 5,
) -> bool:
    """Try to solve an active hCaptcha image-grid puzzle using 2Captcha GridTask.

    Returns True if the challenge iframe closes successfully, False otherwise.
    Never raises — caller falls back to the CaptchaDetected/review path.
    """
    deadline = time.time() + max(total_timeout, 180)
    last_prompt = ""
    rounds_done = 0

    for round_idx in range(1, max_rounds + 1):
        if time.time() >= deadline:
            log.warning("grid solver: total timeout (%ds) after %d rounds",
                        total_timeout, rounds_done)
            return False

        iframe_el, iframe_box = _find_challenge_iframe(page)
        if iframe_el is None:
            log.info("grid solver: challenge iframe gone after %d round(s) — solved",
                     rounds_done)
            return True

        # Poll for the puzzle prompt to appear. hCaptcha's render pipeline
        # often takes 5–20 seconds after submit click to fully mount the
        # image grid: the widget expands, then the images lazy-load, then
        # the prompt text mounts. Don't give up prematurely — this is the
        # #1 reason the solver used to bail on Lever boards.
        prompt = _wait_for_prompt(
            page,
            max_wait=min(30.0, max(5.0, deadline - time.time())),
        )
        if not prompt:
            _dump_hcaptcha_state(page, tag=f"round{round_idx}_no_prompt")
            log.warning("grid solver: round=%d puzzle prompt never appeared; "
                        "aborting (probable invisible-pass OR captcha killed "
                        "by site-side rate limit). See dump above.", round_idx)
            return False

        # For GRID puzzles the prompt text varies round-to-round (the target
        # object changes: motorcycle → traffic light → …), so an unchanged
        # prompt signals a misclassification loop.
        # For COORDS puzzles (shape matching, "click the identical shapes")
        # the prompt is literally always the same string — only the images
        # differ — so this check would fire on every attempt. Suppress it
        # for coords; `max_rounds` (default 5) remains the safety cap.
        kind_probe = _classify_challenge(prompt)
        if kind_probe == "grid" and prompt == last_prompt:
            log.warning("grid solver: prompt unchanged round-to-round — "
                        "probable misclassification loop, aborting")
            return False
        last_prompt = prompt

        # Classify the challenge type and dispatch to the appropriate
        # 2Captcha task (GridTask for image grids, CoordinatesTask for
        # shape / object clicking).
        kind = _classify_challenge(prompt)
        log.info("grid solver: round=%d kind=%s prompt=%r",
                 round_idx, kind, prompt[:140])

        remaining = int(max(30, deadline - time.time()))
        if kind == "grid":
            ok = _do_grid_round(
                page, iframe_box=iframe_box, prompt=prompt,
                api_key=api_key, remaining=remaining,
            )
        elif kind == "coords":
            ok = _do_coords_round(
                page, iframe_box=iframe_box, prompt=prompt,
                api_key=api_key, remaining=remaining,
            )
        else:
            _dump_hcaptcha_state(page, tag=f"round{round_idx}_unknown_kind")
            log.warning("grid solver: unrecognized challenge kind for prompt=%r; "
                        "aborting (add the phrase to _COORDS_PROMPT_PHRASES "
                        "or _GRID_PROMPT_PHRASES if this is a legit puzzle)",
                        prompt[:140])
            return False

        if not ok:
            # Sub-round helper already logged the specific failure.
            return False

        # Click Verify / Next to advance hCaptcha.
        if not _click_advance(page):
            log.warning("grid solver: couldn't click any advance button")

        # Wait for hCaptcha to either close or show next round.
        _jitter(2.0, 3.5)
        rounds_done += 1

    # After max rounds, one last check to catch a late close.
    iframe_el, _ = _find_challenge_iframe(page)
    if iframe_el is None:
        log.info("grid solver: challenge closed after max rounds — solved")
        return True
    log.warning("grid solver: exhausted %d rounds, challenge still open", max_rounds)
    return False


# ── Helpers ──────────────────────────────────────────────────────────────


def _find_challenge_iframe(page: Any) -> tuple[Any, dict | None]:
    """Return the largest visible hCaptcha iframe (the challenge modal).

    Idle widgets are ~300×74 or ~300×300; challenge modals are ~500×570+.
    We require ≥300 on both dimensions to avoid confusing the idle
    checkbox for an active puzzle.
    """
    try:
        candidates = page.locator('iframe[src*="hcaptcha"]').all()
    except Exception:
        return None, None
    best_el, best_box = None, None
    best_area = 0
    for frame_el in candidates[:8]:
        try:
            if not frame_el.is_visible():
                continue
            box = frame_el.bounding_box()
            if not box:
                continue
            if box.get("height", 0) < 300 or box.get("width", 0) < 300:
                continue
            area = box["width"] * box["height"]
            if area > best_area:
                best_area = area
                best_el, best_box = frame_el, box
        except Exception:
            continue
    return best_el, best_box


def _do_grid_round(
    page: Any,
    *,
    iframe_box: dict,
    prompt: str,
    api_key: str,
    remaining: int,
) -> bool:
    """One round of solving via 2Captcha GridTask (image-grid puzzles).

    Returns True on successful click-all-tiles; False if anything fails
    along the way. Never clicks Verify — the caller does that.
    """
    grid = _find_grid_rect(page, iframe_box)
    if grid is None:
        log.warning("grid round: couldn't locate grid rect inside iframe")
        return False
    grid_x, grid_y, grid_w, grid_h, rows, cols = grid

    log.info("grid round: grid=%dx%d at (%d,%d) %dx%d tiles",
             int(grid_w), int(grid_h), int(grid_x), int(grid_y), rows, cols)

    clip = {
        "x": max(int(grid_x), 0),
        "y": max(int(grid_y), 0),
        "width": int(grid_w),
        "height": int(grid_h),
    }
    try:
        png = page.screenshot(clip=clip)
    except Exception as exc:
        log.warning("grid round: screenshot failed: %s", exc)
        return False

    image_b64 = base64.b64encode(png).decode("ascii")

    try:
        tile_indices = _submit_2captcha_grid(
            api_key=api_key, image_b64=image_b64, prompt=prompt,
            rows=rows, cols=cols, timeout=remaining,
        )
    except Exception as exc:
        log.warning("grid round: 2captcha GridTask error: %s", exc)
        return False

    log.info("grid round: worker selected %d tile(s): %s",
             len(tile_indices), tile_indices)

    tile_w = grid_w / cols
    tile_h = grid_h / rows
    for idx in tile_indices:
        if idx < 1 or idx > rows * cols:
            log.debug("grid round: skipping out-of-range tile %d", idx)
            continue
        col = (idx - 1) % cols
        row = (idx - 1) // cols
        cx = grid_x + tile_w * (col + 0.5)
        cy = grid_y + tile_h * (row + 0.5)
        try:
            page.mouse.click(cx, cy)
        except Exception as exc:
            log.debug("grid round: click tile=%d failed: %s", idx, exc)
        _jitter(0.25, 0.55)
    return True


def _do_coords_round(
    page: Any,
    *,
    iframe_box: dict,
    prompt: str,
    api_key: str,
    remaining: int,
) -> bool:
    """One round of solving via 2Captcha CoordinatesTask (click N arbitrary points).

    Used for shape-matching puzzles ("click the TWO shapes that are
    identical") and object-selection puzzles ("click on the object that
    appears only once"). Returns True on success; False on any failure.
    """
    area = _find_puzzle_area_in_iframe(page, iframe_box)
    if area is None:
        log.warning("coords round: couldn't locate puzzle area inside iframe")
        return False
    ax, ay, aw, ah = area

    log.info("coords round: puzzle area %dx%d at (%d,%d)",
             int(aw), int(ah), int(ax), int(ay))

    clip = {
        "x": max(int(ax), 0),
        "y": max(int(ay), 0),
        "width": int(aw),
        "height": int(ah),
    }
    try:
        png = page.screenshot(clip=clip)
    except Exception as exc:
        log.warning("coords round: screenshot failed: %s", exc)
        return False

    image_b64 = base64.b64encode(png).decode("ascii")

    try:
        points = _submit_2captcha_coordinates(
            api_key=api_key, image_b64=image_b64, prompt=prompt,
            timeout=remaining,
        )
    except Exception as exc:
        log.warning("coords round: 2captcha CoordinatesTask error: %s", exc)
        return False

    if not points:
        log.warning("coords round: 2captcha returned zero points")
        return False

    log.info("coords round: worker returned %d point(s): %s",
             len(points), points)

    # Save an annotated debug screenshot so we can eyeball whether 2Captcha's
    # chosen points land on the actual shapes. This is what tells us if the
    # round failed because of (a) bad 2Captcha worker choices, (b) wrong
    # puzzle-area detection, or (c) hCaptcha rejecting correct clicks
    # (IP reputation). Saved to state/failed_submits/.
    _save_annotated_screenshot(png, points, tag="coords_clicks")

    for (rx, ry) in points:
        abs_x = ax + rx
        abs_y = ay + ry
        try:
            page.mouse.click(abs_x, abs_y)
        except Exception as exc:
            log.debug("coords round: click (%d,%d) failed: %s",
                      int(abs_x), int(abs_y), exc)
        _jitter(0.25, 0.55)
    return True


def _find_puzzle_area_in_iframe(
    page: Any, iframe_box: dict | None
) -> tuple[float, float, float, float] | None:
    """Locate the visible puzzle content container inside an hCaptcha frame.

    Returns (abs_x, abs_y, width, height) in PAGE-ABSOLUTE coords, or None.

    The enclave iframe often covers the full viewport (1280×900) as an
    overlay backdrop — we can't just use the iframe bounding box. We
    have to look INSIDE the frame for the element that's actually
    showing the puzzle.

    Strategy:
      1. Try common hCaptcha selectors (#challenge, [role=dialog], .panel).
      2. Fallback: find the largest visible body descendant that is NOT
         full-viewport sized (those are overlays, not the puzzle).
    """
    if iframe_box is None:
        return None

    for fr in page.frames:
        url_l = (getattr(fr, "url", "") or "").lower()
        if "hcaptcha" not in url_l:
            continue
        try:
            rect = fr.evaluate(
                """() => {
                    const W = window.innerWidth;
                    const H = window.innerHeight;
                    const visible = (el) => {
                        const cs = window.getComputedStyle(el);
                        return cs.display !== 'none' &&
                               cs.visibility !== 'hidden' &&
                               parseFloat(cs.opacity) > 0;
                    };

                    // Preferred selectors — hCaptcha's current DOM uses
                    // a few stable IDs / roles / class substrings.
                    const selectors = [
                        '#challenge',
                        'main',
                        '[role="main"]',
                        '[role="dialog"]',
                        '.challenge-view',
                        '.challenge-panel',
                        '.challenge',
                        '.panel',
                        '[class*="challenge"]',
                        '[class*="panel"]',
                    ];
                    for (const s of selectors) {
                        const el = document.querySelector(s);
                        if (!el || !visible(el)) continue;
                        const r = el.getBoundingClientRect();
                        if (r.width < 200 || r.height < 200) continue;
                        // Skip full-viewport overlays
                        if (r.width >= W * 0.95 && r.height >= H * 0.95) continue;
                        return {x: r.x, y: r.y, width: r.width, height: r.height};
                    }

                    // Fallback: largest non-overlay visible element.
                    const body = document.body;
                    if (!body) return null;
                    let best = null, bestArea = 0;
                    const all = body.querySelectorAll('*');
                    for (const el of all) {
                        if (!visible(el)) continue;
                        const r = el.getBoundingClientRect();
                        if (r.width < 200 || r.height < 200) continue;
                        if (r.width >= W * 0.95 && r.height >= H * 0.95) continue;
                        const area = r.width * r.height;
                        if (area > bestArea) {
                            bestArea = area;
                            best = {x: r.x, y: r.y, width: r.width, height: r.height};
                        }
                    }
                    return best;
                }"""
            )
        except Exception:
            rect = None
        if not rect:
            continue
        # The enclave iframe covers the viewport; elements inside have
        # frame-relative coords that equal page-absolute coords here
        # (iframe top-left at 0,0). But still add offset for correctness
        # in case an hCaptcha variant uses a smaller iframe.
        return (
            iframe_box.get("x", 0) + rect["x"],
            iframe_box.get("y", 0) + rect["y"],
            rect["width"],
            rect["height"],
        )

    return None


def _wait_for_prompt(page: Any, *, max_wait: float) -> str:
    """Poll the hCaptcha frames for a puzzle prompt to render.

    hCaptcha's challenge pipeline is asynchronous:
      1. Submit click fires hCaptcha.execute().
      2. Widget iframe expands; "enclave" loads in a nested frame.
      3. Image tiles fetch (CDN round-trip, can be 5–10s on slow links).
      4. Prompt text ("Please click each image containing ...") mounts.

    A naïve single-shot prompt check misses this window on most Lever
    boards. Poll every ~1.5s for up to `max_wait` seconds, and if no
    puzzle appears and we only see the idle checkbox widget, click it
    to force hCaptcha to start the challenge.
    """
    deadline = time.time() + max_wait
    next_checkbox_attempt = time.time() + 6.0   # wait a bit before forcing

    while time.time() < deadline:
        prompt = _extract_prompt(page)
        if prompt:
            return prompt
        # After the grace period, try to trigger the puzzle by clicking the
        # "I am human" checkbox — on some configurations the submit click
        # produces only the checkbox, not the image challenge.
        if time.time() >= next_checkbox_attempt:
            if _click_hcaptcha_checkbox(page):
                log.info("grid solver: clicked hCaptcha checkbox to trigger puzzle")
                next_checkbox_attempt = time.time() + 10.0   # don't re-spam
        time.sleep(1.5)

    return ""


def _click_hcaptcha_checkbox(page: Any) -> bool:
    """Click the 'I am human' checkbox inside hCaptcha's anchor iframe.

    Some hCaptcha configurations render a checkbox widget after submit
    rather than immediately showing the image grid. Clicking this
    checkbox fires the challenge callback and mounts the puzzle.
    """
    for fr in page.frames:
        url_l = (getattr(fr, "url", "") or "").lower()
        # hCaptcha's anchor (checkbox) iframe src typically contains
        # "frame=checkbox" or ends in "/anchor".
        if "hcaptcha" not in url_l:
            continue
        if not ("frame=checkbox" in url_l or "/anchor" in url_l):
            continue
        try:
            locator = fr.locator(
                '#checkbox, div[role="checkbox"], [aria-label*="robot" i]'
            )
            if locator.count() == 0:
                continue
            cb = locator.first
            if not cb.is_visible():
                continue
            cb.click(timeout=2_500)
            return True
        except Exception:
            continue
    return False


def _extract_prompt(page: Any) -> str:
    """Extract the challenge prompt from the hCaptcha iframe's body text."""
    for fr in page.frames:
        url_l = (getattr(fr, "url", "") or "").lower()
        if "hcaptcha" not in url_l:
            continue
        try:
            text = fr.evaluate("() => (document.body?.innerText || '').trim()")
        except Exception:
            continue
        if not text:
            continue
        lower = text.lower()
        if not any(p in lower for p in _PROMPT_PHRASES):
            continue
        for line in text.splitlines():
            line = line.strip()
            if len(line) >= 10 and any(p in line.lower() for p in _PROMPT_PHRASES):
                return line[:180]
        return text.strip()[:180]
    return ""


def _find_grid_rect(
    page: Any, iframe_box: dict | None
) -> tuple[float, float, float, float, int, int] | None:
    """Locate the image-grid container inside the hCaptcha challenge iframe.

    Returns (abs_x, abs_y, width, height, rows, cols) or None.

    Primary path: query the challenge iframe's DOM for the grid container
    and count the image cells to determine rows/cols.

    Fallback: approximate — the grid is the middle ~67% of the iframe
    vertically (top ~18% = prompt header, bottom ~15% = verify footer).
    Assume 3x3 if we can't count cells.
    """
    if iframe_box is None:
        return None

    for fr in page.frames:
        url_l = (getattr(fr, "url", "") or "").lower()
        if "hcaptcha" not in url_l:
            continue
        try:
            info = fr.evaluate(
                """() => {
                    // hCaptcha uses different class names across versions —
                    // try several selectors for the grid container.
                    const selectors = [
                        '.task-grid',
                        '.challenge-answer .challenge-grid',
                        '.challenge-answer',
                        '[class*="challenge-grid"]',
                        '[class*="task-grid"]',
                    ];
                    let container = null;
                    for (const s of selectors) {
                        const el = document.querySelector(s);
                        if (el) { container = el; break; }
                    }
                    if (!container) return null;
                    const r = container.getBoundingClientRect();
                    // Count cell elements — .task-image / .challenge-image /
                    // direct img children are common markers.
                    let cells = container.querySelectorAll(
                        '.task-image, .challenge-image, [class*="task-image"]'
                    );
                    if (cells.length === 0) {
                        cells = container.querySelectorAll('img');
                    }
                    return {
                        x: r.x, y: r.y, width: r.width, height: r.height,
                        cells: cells.length,
                    };
                }"""
            )
        except Exception:
            info = None
        if not info:
            continue
        # Map iframe-local coords to page-absolute by adding the iframe's offset.
        abs_x = iframe_box["x"] + info["x"]
        abs_y = iframe_box["y"] + info["y"]
        rows, cols = _rows_cols_for_count(info.get("cells", 0))
        return (abs_x, abs_y, info["width"], info["height"], rows, cols)

    # Fallback approximation when JS couldn't reach inside the iframe.
    top_pad = iframe_box["height"] * 0.18
    bot_pad = iframe_box["height"] * 0.15
    return (
        iframe_box["x"],
        iframe_box["y"] + top_pad,
        iframe_box["width"],
        iframe_box["height"] - top_pad - bot_pad,
        3, 3,
    )


def _rows_cols_for_count(n: int) -> tuple[int, int]:
    """Map a cell count to (rows, cols). Defaults to 3×3 when uncertain."""
    if n == 9:
        return 3, 3
    if n == 16:
        return 4, 4
    if n == 12:
        return 3, 4   # rare; hCaptcha occasionally shows landscape grids
    return 3, 3


def _click_advance(page: Any) -> bool:
    """Click the Verify / Next / Submit / Skip button inside the challenge iframe."""
    for fr in page.frames:
        url_l = (getattr(fr, "url", "") or "").lower()
        if "hcaptcha" not in url_l:
            continue
        for btn_text in _ADVANCE_BUTTONS:
            try:
                locator = fr.locator(
                    f'div[role="button"]:has-text("{btn_text}"), '
                    f'button:has-text("{btn_text}")'
                )
                if locator.count() == 0:
                    continue
                btn = locator.first
                if not btn.is_visible():
                    continue
                btn.click(timeout=3_000)
                log.debug("grid solver: clicked %s inside challenge iframe", btn_text)
                return True
            except Exception:
                continue
    return False


def _submit_2captcha_grid(
    *,
    api_key: str,
    image_b64: str,
    prompt: str,
    rows: int,
    cols: int,
    timeout: int,
) -> list[int]:
    """Submit a GridTask to 2Captcha's JSON API v2; return tile indices.

    Uses the shared `solve_2captcha_task` helper so it inherits the same
    timeout + error handling as other v2 task types.
    """
    from autoapply.execute.captcha_solver import solve_2captcha_task

    task_params: dict[str, Any] = {
        "body": image_b64,
        "comment": prompt,
        "rows": rows,
        "columns": cols,
    }
    click = solve_2captcha_task(
        task_type="GridTask",
        task_params=task_params,
        api_key=api_key,
        timeout=timeout,
        # GridTask solution uses `click: [...]` — list of 1-indexed tile numbers.
        solution_keys=("click",),
    )
    if not isinstance(click, (list, tuple)):
        raise RuntimeError(f"2captcha GridTask: unexpected solution shape: {click!r}")
    return [int(x) for x in click]


def _submit_2captcha_coordinates(
    *,
    api_key: str,
    image_b64: str,
    prompt: str,
    timeout: int,
) -> list[tuple[int, int]]:
    """Submit a CoordinatesTask to 2Captcha JSON v2; return pixel click points.

    Docs: https://2captcha.com/api-docs/coordinates

    The `solution.coordinates` field is a list of `{x, y}` objects
    representing the pixel positions (relative to the submitted image)
    the worker chose as matching the prompt. For an hCaptcha shape-pair
    challenge like "click the TWO shapes that are identical", we expect
    ~2 points back.
    """
    from autoapply.execute.captcha_solver import solve_2captcha_task

    raw = solve_2captcha_task(
        task_type="CoordinatesTask",
        task_params={"body": image_b64, "comment": prompt},
        api_key=api_key,
        timeout=timeout,
        solution_keys=("coordinates",),
    )
    if not isinstance(raw, (list, tuple)):
        raise RuntimeError(
            f"2captcha CoordinatesTask: unexpected solution shape: {raw!r}"
        )
    points: list[tuple[int, int]] = []
    for item in raw:
        if isinstance(item, dict):
            x, y = item.get("x"), item.get("y")
            if x is not None and y is not None:
                points.append((int(x), int(y)))
    return points


def _jitter(low: float, high: float) -> None:
    """Short randomized delay between user-like actions."""
    time.sleep(random.uniform(low, high))


def _save_annotated_screenshot(
    png_bytes: bytes,
    points: list[tuple[int, int]],
    *,
    tag: str,
) -> None:
    """Save a copy of the challenge screenshot with red circles at `points`.

    Used to visually verify whether 2Captcha's worker picked the right
    pixels. If the circles are clearly ON the shapes → our clicks were
    correct and hCaptcha rejected anyway (IP reputation issue). If they
    miss → 2Captcha mis-classified, and we need different task params or
    a larger/tighter screenshot crop.

    Pillow is a soft dep — if it's not installed we just save the raw PNG.
    """
    from pathlib import Path
    import hashlib as _hashlib

    out_dir = Path("state") / "failed_submits"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return
    h = _hashlib.md5(png_bytes[:4096] + str(points).encode()).hexdigest()[:10]
    out_path = out_dir / f"{tag}_{h}.png"

    try:
        from PIL import Image, ImageDraw   # type: ignore[import]
    except ImportError:
        # No Pillow — just save the raw screenshot and skip annotation.
        try:
            out_path.write_bytes(png_bytes)
            log.warning("saved raw challenge screenshot (no annotation — "
                        "install pillow for marked dots): %s", out_path)
        except Exception as exc:
            log.debug("raw screenshot save failed: %s", exc)
        return

    try:
        from io import BytesIO
        im = Image.open(BytesIO(png_bytes)).convert("RGB")
        draw = ImageDraw.Draw(im)
        for (x, y) in points:
            r = 14
            # Red circle + crosshair for each clicked coordinate
            draw.ellipse([x - r, y - r, x + r, y + r], outline="red", width=3)
            draw.line([x - r - 4, y, x + r + 4, y], fill="red", width=2)
            draw.line([x, y - r - 4, x, y + r + 4], fill="red", width=2)
        im.save(out_path)
        log.warning("saved annotated challenge screenshot (red dots = "
                    "clicked points): %s", out_path)
    except Exception as exc:
        log.debug("annotated screenshot failed: %s", exc)


def _dump_hcaptcha_state(page: Any, *, tag: str) -> None:
    """Emit an exhaustive diagnostic of hCaptcha's current state.

    Logs + saves a screenshot so we can reason about *why* the puzzle
    never appeared. Covers:
      - Page URL + whether we're still on the apply form.
      - Every hCaptcha-origin iframe: URL, bounding box, visible flag.
      - Inner text of each hCaptcha frame (first 500 chars).
      - Submit button visibility.
      - Whether a success-page phrase is already present.

    Never raises — all probes are defensive.
    """
    from pathlib import Path
    import hashlib as _hashlib

    try:
        url = page.url or ""
        log.warning("── hCaptcha state dump (%s) ──", tag)
        log.warning("  page.url = %s", url)

        # Submit button state — is the form still showing?
        try:
            btn = page.locator("#btn-submit").first
            vis = btn.is_visible(timeout=500) if btn.count() > 0 else False
            log.warning("  #btn-submit visible = %s", vis)
        except Exception:
            pass

        # hCaptcha iframes — URL + bounding box
        try:
            frames = page.locator('iframe[src*="hcaptcha"]').all()
            log.warning("  hCaptcha iframes: count=%d", len(frames))
            for i, fr_el in enumerate(frames[:8]):
                try:
                    src = fr_el.get_attribute("src") or ""
                    box = fr_el.bounding_box()
                    visible = fr_el.is_visible()
                    frame_kind = "enclave" if "frame=enclave" in src else (
                        "checkbox" if "frame=checkbox" in src else
                        "challenge" if "frame=challenge" in src else
                        "anchor" if "/anchor" in src else "other"
                    )
                    log.warning(
                        "    [%d] kind=%s visible=%s box=%s src=%s",
                        i, frame_kind, visible, box, src[:140],
                    )
                except Exception as exc:
                    log.warning("    [%d] probe error: %s", i, exc)
        except Exception as exc:
            log.warning("  iframe enumeration failed: %s", exc)

        # Inner text of each hCaptcha frame
        for i, fr in enumerate(page.frames):
            u = (getattr(fr, "url", "") or "").lower()
            if "hcaptcha" not in u:
                continue
            try:
                txt = fr.evaluate(
                    "() => (document.body && document.body.innerText) || ''"
                )
                txt = (txt or "").strip()
                log.warning(
                    "  frame#%d text (%d chars): %r",
                    i, len(txt), txt[:500],
                )
            except Exception as exc:
                log.warning("  frame#%d text probe failed: %s", i, exc)

        # Check if we're already on a success page
        try:
            body = (page.inner_text("body") or "").lower()
            for phrase in (
                "your application has been submitted",
                "thank you for applying",
                "application received",
            ):
                if phrase in body:
                    log.warning("  POSSIBLE SUCCESS — page body contains %r",
                                phrase)
                    break
        except Exception:
            pass

        # Screenshot for visual inspection
        try:
            shot_dir = Path("state") / "failed_submits"
            shot_dir.mkdir(parents=True, exist_ok=True)
            h = _hashlib.md5(f"{url}-{tag}".encode()).hexdigest()[:10]
            shot_path = shot_dir / f"hcaptcha_dump_{h}.png"
            page.screenshot(path=str(shot_path), full_page=True)
            log.warning("  screenshot: %s", shot_path)
        except Exception as exc:
            log.warning("  screenshot failed: %s", exc)

        log.warning("── end dump ──")
    except Exception as exc:
        # Never let the diagnostic itself break the solver.
        log.debug("hcaptcha state dump raised: %s", exc)
