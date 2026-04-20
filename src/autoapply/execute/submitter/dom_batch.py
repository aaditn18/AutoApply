"""Stage-2 DOM batch resolver — fills SPA-injected fields via one LLM call.

Context
-------
The new ``job-boards.greenhouse.io`` React SPA injects tenant-specific
form fields that are NOT in Greenhouse's ``/boards/.../jobs/NN?questions=true``
API response. Examples we've hit: ``School*`` (async React-Select) on
jjsnackfoods, ``Gender*`` (EEO React-Select) on Axon/Smartsheet, ``Location
(City)*`` on Fanatics (handled by ``label_fallback.py``), and various
``How did you hear about us?`` SPA injections.

The Stage-1 batch (see :mod:`autoapply.answers.llm_batch`) runs
pre-navigation over API questions only. This module runs after resume
upload + SPA re-render, when the DOM has settled with the full set of
required fields. It:

  1. Scrapes empty required ``<input>`` / ``<select>`` / ``<textarea>``
     elements.
  2. For each React-Select detected, clicks to open its listbox and
     captures the option strings — then closes the dropdown so the
     scrape doesn't interfere with later fills.
  3. Builds a :class:`BatchQuestion` list and calls
     :func:`autoapply.answers.llm_batch.resolve_batch` in ONE shot.
  4. Fills each answered field via ``fill_combobox`` / ``fill_field`` /
     native ``<select>`` select_option.

Fields already handled elsewhere:
  * Machine-key fields (first_name, email, ...) — never empty at this point.
  * Fields in the ``already_filled_keys`` set — skipped.
  * Non-required empty fields — left blank per the "don't guess optional
    fields" policy.
  * Checkboxes — not yet handled (different mechanism; follow-up work).

Dependencies on other submitter modules: imports at call time to avoid
a hard cross-module cycle on module load.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from autoapply.answers.llm_batch import BatchQuestion, resolve_batch
from autoapply.rules import load_rules

from .util import jitter


if TYPE_CHECKING:
    from autoapply.profile.schema import Profile

log = logging.getLogger(__name__)


# ─── Detection ────────────────────────────────────────────────────────────


# Field kinds we're willing to have the LLM fill. Checkboxes and radios
# are left out intentionally — those need a different scraping + filling
# pattern (checked/unchecked rather than value-from-options).
_SUPPORTED_INPUT_TYPES = {"text", "email", "tel", "search", "url", "number", ""}


@dataclass
class _DomField:
    """Cached info about one DOM element that needs filling."""
    element_id: str            # dedup key — the [id] or [name] attr
    label: str                 # human-readable label for the LLM prompt
    kind: str                  # "select" | "multi_select" | "text" | "textarea"
    options: list[str]         # non-empty only for kind=="select"/"multi_select"
    locator: Any               # Playwright Locator for the actual input

    # For ``kind == "multi_select"``: a list of Playwright Locators for
    # the individual checkbox inputs. The scraper fills these in when
    # detecting a required checkbox group (Greenhouse's ``race``,
    # ``US states'' lists, ``locations you're committed to`` etc.).
    # Left None for non-checkbox kinds.
    checkbox_locators: list[Any] | None = None


def collect_empty_required_fields(
    page: Any, already_filled_keys: set[str]
) -> list[_DomField]:
    """Walk the DOM for required fields that are still empty.

    Only scans fields visible on screen — collapsed sections (rarely
    required) are skipped since we can't interact with them reliably
    anyway. Dedupes by ``id`` / ``name``.

    Options scraping for React-Select happens lazily (later, in
    :func:`_scrape_select_options`) — not here — because opening a
    dropdown is slow and we only want to do it if we're going to
    send the field to the LLM.
    """
    collected: dict[str, _DomField] = {}

    def _tag(el: Any) -> str:
        try:
            return (
                el.evaluate("e => e.tagName && e.tagName.toLowerCase()") or ""
            )
        except Exception:
            return ""

    def _is_required(el: Any) -> bool:
        try:
            if el.evaluate("e => e.required") is True:
                return True
            attr = el.get_attribute("aria-required") or ""
            if (attr or "").lower() == "true":
                return True
            # Greenhouse / Lever often put a ``.required`` marker in the
            # containing form group.
            has_marker = el.evaluate(
                "e => !!(e.closest('.application-question')?.querySelector('.required'))"
            )
            return bool(has_marker)
        except Exception:
            return False

    def _has_value(el: Any, tag: str) -> bool:
        """True if the field already has a usable non-placeholder value.

        For React-Select, also reads the ``singleValue`` render so we
        don't re-fill a combobox that Stage-1 or earlier passes already
        resolved. The check mirrors the one in ``diagnostics.py``.
        """
        try:
            if tag == "select":
                raw = el.evaluate("e => e.value") or ""
                return bool(raw) and raw.strip().lower() not in (
                    "", "select", "select...", "choose", "choose...",
                )
            # Read the React-Select singleValue text too — a filled
            # combobox has empty .value but a visible singleValue span.
            info = el.evaluate("""
                (el) => {
                    const v = el.value || '';
                    const wrapper = el.closest(
                        '[class*="container"], [class*="Select"], '
                        + '[class*="-control"]'
                    );
                    let sv = '';
                    if (wrapper) {
                        const node = wrapper.querySelector(
                            '[class*="singleValue"], '
                            + '[class*="single-value"], '
                            + '[class*="singleval"]'
                        );
                        if (node) sv = (node.innerText || '').trim();
                    }
                    return {v: v.trim(), sv: sv};
                }
            """) or {}
            val = (info.get("v") or "").strip()
            sv = (info.get("sv") or "").strip()
            return bool(val) or bool(sv)
        except Exception:
            return False

    def _label_of(el: Any) -> str:
        """Priority: aria-label → aria-labelledby → <label for> →
        ancestor <label> → placeholder."""
        try:
            al = (el.get_attribute("aria-label") or "").strip()
            if al:
                return al
        except Exception:
            pass
        try:
            alb = (el.get_attribute("aria-labelledby") or "").strip()
            if alb:
                for ref in alb.split():
                    loc = page.locator(f'[id="{ref}"]')
                    if loc.count() > 0:
                        txt = loc.first.inner_text().strip()
                        if txt:
                            return txt
        except Exception:
            pass
        try:
            eid = el.get_attribute("id") or ""
            if eid:
                lbl = page.locator(f'label[for="{eid}"]')
                if lbl.count() > 0:
                    t = lbl.first.inner_text().strip()
                    if t:
                        return t
        except Exception:
            pass
        try:
            anc = el.locator("xpath=ancestor::label[1]")
            if anc.count() > 0:
                t = anc.first.inner_text().strip()
                if t:
                    return t
        except Exception:
            pass
        try:
            return (el.get_attribute("placeholder") or "").strip()
        except Exception:
            return ""

    # Enumerate candidate elements. Selectors deliberately include
    # ``[id]``-only fields (new Greenhouse SPA) and textarea/select.
    # Checkboxes are included so we can auto-check single required
    # consent / T&C boxes (GDPR, privacy, arbitration). Multi-select
    # checkbox groups (state lists, "which locations") are handled as
    # a separate post-processing pass — see `_collect_checkbox_groups`.
    selectors = (
        "input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=file])",
        "select",
        "textarea",
    )
    elements: list[Any] = []
    for sel in selectors:
        try:
            elements.extend(page.locator(sel).all())
        except Exception as exc:
            log.debug("dom_batch: enumerate %r failed: %s", sel, exc)

    for el in elements:
        try:
            eid = el.get_attribute("id") or ""
            ename = el.get_attribute("name") or ""
            dedup_key = eid or ename
            if not dedup_key or dedup_key in collected:
                continue
            if dedup_key in already_filled_keys:
                continue

            tag = _tag(el)
            input_type = (el.get_attribute("type") or "").lower()

            # Checkboxes get a dedicated kind and filler — the element
            # ISN'T skipped here but its kind becomes "checkbox" below.
            # Radios and file inputs are always skipped.
            if tag == "input" and input_type == "radio":
                continue
            if tag == "input" and input_type == "file":
                continue
            if tag == "input" and input_type not in _SUPPORTED_INPUT_TYPES \
                    and input_type != "checkbox":
                continue

            # Visibility gate.
            try:
                if not el.is_visible(timeout=500):
                    continue
            except Exception:
                continue

            # Required gate.
            if not _is_required(el):
                continue

            # Already has a value → skip. For checkboxes, "has value"
            # means "checked" (_has_value reads ``el.checked``).
            if tag == "input" and input_type == "checkbox":
                try:
                    if el.evaluate("e => e.checked") is True:
                        continue
                except Exception:
                    pass
            elif _has_value(el, tag):
                continue

            label = _label_of(el)
            if not label:
                continue  # no way for LLM to reason about it

            # Kind classification.
            if tag == "input" and input_type == "checkbox":
                # Standalone required checkbox (T&C, GDPR, privacy).
                # Group-style checkbox lists (multi-state selection)
                # would also land here but each checkbox gets its own
                # entry with the surrounding label; the LLM will
                # answer Yes/No per checkbox based on context.
                kind = "checkbox"
            elif tag == "select":
                kind = "select"
            elif tag == "textarea":
                kind = "textarea"
            else:
                # Could be React-Select; detection happens at option-
                # scrape time. For now, mark as "text" and upgrade to
                # "select" if we detect the wrapper.
                kind = "text"
                if _looks_like_react_select(el):
                    kind = "select"

            collected[dedup_key] = _DomField(
                element_id=dedup_key,
                label=label,
                kind=kind,
                options=[],  # scraped lazily below
                locator=el,
            )
        except Exception as exc:
            log.debug("dom_batch: element scan error: %s", exc)

    return list(collected.values())


def _looks_like_react_select(el: Any) -> bool:
    """True when the input is wrapped by a React-Select control.

    Same heuristic as :func:`field_fill._is_react_select`, inlined here
    to avoid importing ``.field_fill`` (which in turn imports this
    module would create a cycle — the import is done at call site in
    driver.py).
    """
    try:
        role = (el.get_attribute("role") or "").lower()
        if role == "combobox":
            return True
    except Exception:
        return False
    try:
        anc = el.locator(
            "xpath=ancestor::*["
            "contains(@class,'select__control') or "
            "contains(@class,'Select__control') or "
            "contains(@class,'react-select') or "
            "contains(@class,'-control')][1]"
        )
        if anc.count() > 0:
            return True
    except Exception:
        pass
    return False


# ─── Option scraping ──────────────────────────────────────────────────────


def _scrape_select_options(page: Any, el: Any, max_opts: int = 80) -> list[str]:
    """Open a React-Select / native-select, capture options, close.

    React-Select renders its menu in a portal on demand. We click the
    input to open, wait briefly, read ``[role='option']`` text, then
    press Escape to close. Native ``<select>`` elements have ``<option>``
    children readable without interaction.

    Async-typeahead handling: some dropdowns (School lookups, Country
    pickers with 10k+ entries) don't populate options until the user
    types. If the initial click+wait gives zero options, we treat the
    field as a freetext target — the LLM returns a string, and our
    ``fill_combobox`` path types + picks from the async-loaded menu
    at fill time. Returning ``[]`` here is the signal for that path.

    Returns a list of option labels (trimmed, empty strings excluded).
    Bounded to ``max_opts`` to keep prompt size under control for 200+
    option lists (e.g., country codes on phone-number pickers).
    """
    # Native <select>: read <option> directly, no click needed.
    try:
        tag = el.evaluate("e => e.tagName.toLowerCase()") or ""
    except Exception:
        tag = ""
    if tag == "select":
        try:
            opts = el.evaluate("""
                (el) => Array.from(el.querySelectorAll('option'))
                    .map(o => (o.textContent || '').trim())
                    .filter(t => t.length > 0)
            """) or []
            return list(opts)[:max_opts]
        except Exception as exc:
            log.debug("dom_batch: native select options failed: %s", exc)
            return []

    # React-Select path.
    try:
        el.scroll_into_view_if_needed(timeout=2_000)
    except Exception:
        pass
    try:
        el.click(timeout=3_000)
    except Exception:
        # Hidden input — try ancestor control click.
        try:
            ctrl = el.locator(
                "xpath=ancestor::*["
                "contains(@class,'select__control') or "
                "contains(@class,'-control')][1]"
            )
            if ctrl.count() > 0:
                ctrl.first.click(timeout=2_500)
        except Exception:
            pass

    jitter(0.3, 0.6)

    # Scope to this combobox's listbox.
    aria_controls = ""
    try:
        aria_controls = el.get_attribute("aria-controls") or ""
    except Exception:
        pass

    if aria_controls:
        options_loc = page.locator(f'[id="{aria_controls}"] [role="option"]')
    else:
        options_loc = el.locator(
            "xpath=ancestor::*["
            "contains(@class,'select__container') or "
            "contains(@class,'-container')][1]"
            "//*[@role='option']"
        )

    # Async-load tolerance.
    for _ in range(8):
        if options_loc.count() > 0:
            break
        jitter(0.2, 0.3)

    out: list[str] = []
    count = options_loc.count()
    for i in range(min(count, max_opts)):
        try:
            txt = options_loc.nth(i).inner_text().strip()
            if txt:
                out.append(txt)
        except Exception:
            continue

    # Close the dropdown so subsequent scrapes don't pick up this one's
    # options.
    try:
        el.press("Escape")
    except Exception:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
    jitter(0.1, 0.3)

    return out


def _scrape_async_typeahead_options(
    page: Any, el: Any, seed_text: str, max_opts: int = 30,
) -> list[str]:
    """Type a seed string into an async-typeahead React-Select, then
    scrape the options that appear.

    Used when the plain ``_scrape_select_options`` pass returns empty
    because the dropdown requires user input before it loads anything
    (Schools/Universities typeahead, Country typeahead, etc.). Typing
    a discriminating prefix (e.g., "univ" for School, "United States"
    for Country) triggers the XHR fetch, and the menu renders
    predictable results.

    Returns a list of option label strings (trimmed, empty-stripped).
    Bounded to ``max_opts`` so the subsequent prompt stays small.
    """
    if not seed_text:
        return []
    # Open the dropdown.
    try:
        el.scroll_into_view_if_needed(timeout=2_000)
    except Exception:
        pass
    try:
        el.click(timeout=3_000)
    except Exception:
        try:
            el.focus(timeout=1_000)
        except Exception:
            pass
    jitter(0.2, 0.4)
    try:
        el.fill("", timeout=2_000)
    except Exception:
        pass
    try:
        el.type(seed_text, delay=40)
    except Exception:
        return []
    # Async load — wait a bit longer here because the XHR fetch takes
    # hundreds of ms on slow backends.
    jitter(0.8, 1.2)

    aria_controls = ""
    try:
        aria_controls = el.get_attribute("aria-controls") or ""
    except Exception:
        pass
    if aria_controls:
        options_loc = page.locator(f'[id="{aria_controls}"] [role="option"]')
    else:
        options_loc = el.locator(
            "xpath=ancestor::*[contains(@class,'-container')][1]"
            "//*[@role='option']"
        )
    for _ in range(8):
        if options_loc.count() > 0:
            break
        jitter(0.2, 0.3)

    out: list[str] = []
    count = options_loc.count()
    for i in range(min(count, max_opts)):
        try:
            txt = options_loc.nth(i).inner_text().strip()
            if txt:
                out.append(txt)
        except Exception:
            continue

    # Clear what we typed so subsequent fills start clean.
    try:
        el.fill("", timeout=1_500)
    except Exception:
        pass
    try:
        el.press("Escape")
    except Exception:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
    jitter(0.1, 0.3)

    return out


# ─── Entry point ──────────────────────────────────────────────────────────


def batch_resolve_dom_fields(
    *,
    page: Any,
    profile: "Profile",
    answer_bank_yaml: str,
    track: str = "swe",
    company: str = "",
    job_title: str = "",
    already_filled_keys: set[str] | None = None,
) -> dict[str, Any]:
    """Scrape empty required fields, batch-LLM, fill.

    Returns an audit dict::

        {
          "scraped_count": N,
          "filled_count": M,
          "model_used": "gemini-2.5-flash-lite",
          "cascade_trace": [...],
          "error": "",
          "per_field": [{field_id, label, value, source, confidence}, ...],
        }

    Callers log this under the same "batch-llm" channel as Stage-1. The
    ``per_field`` list is the audit trail — which DOM-injected questions
    were resolved by the LLM and what answer they got.
    """
    already_filled_keys = already_filled_keys or set()

    audit: dict[str, Any] = {
        "scraped_count": 0,
        "filled_count": 0,
        "model_used": "",
        "cascade_trace": [],
        "error": "",
        "per_field": [],
    }

    fields = collect_empty_required_fields(page, already_filled_keys)
    audit["scraped_count"] = len(fields)

    if not fields:
        return audit

    log.info(
        "dom_batch: scraped %d empty required fields — %s",
        len(fields),
        [f"{f.element_id}({f.kind})" for f in fields][:8],
    )

    # Scrape options for select kinds (expensive — click to open).
    # Classify the label first so we can pick a useful seed string for
    # async typeaheads (Schools, Countries) that render no options until
    # the user types.
    from autoapply.answers.classifier import classify as _classify_for_seed
    from autoapply.answers.types import QuestionType as _QT
    for f in fields:
        if f.kind != "select":
            continue
        f.options = _scrape_select_options(page, f.locator)
        if f.options:
            continue

        # Empty-on-open: probably an async typeahead. Try a seed based
        # on the classifier's guess at the field type.
        try:
            classified = _classify_for_seed(f.label)
        except Exception:
            classified = None
        seed = ""
        if classified is not None:
            if classified.type is _QT.SCHOOL:
                # 'university' loads most US schools; 'maryland' narrows faster.
                seed = "university of mary"
            elif classified.type is _QT.MAJOR:
                seed = "computer"
            elif classified.type is _QT.DEGREE:
                seed = "bachelor"
            # (No CURRENT_COUNTRY classifier type yet — falls through.)
        if seed:
            f.options = _scrape_async_typeahead_options(page, f.locator, seed)
            if f.options:
                log.info(
                    "dom_batch: async-typeahead seed=%r loaded %d options for %s",
                    seed, len(f.options), f.element_id,
                )
                continue

        # Still nothing — downgrade to text so the LLM writes a freetext
        # answer the combobox filler can type + async-filter.
        log.debug(
            "dom_batch: no options scraped for %s — downgrading to text",
            f.element_id,
        )
        f.kind = "text"

    # Pre-resolve pass — for each scraped field, try the deterministic
    # classifier + profile + bank pipeline BEFORE sending to the LLM.
    # This catches fields like School/Degree/Major/Graduation that have
    # unambiguous answers directly in the profile — saves tokens AND
    # avoids the LLM's over-cautious ``needs_review`` habit on text
    # fields without scraped options.
    #
    # Each pre-resolved field is filled immediately and removed from
    # the batch. Whatever remains goes to the LLM.
    preresolved_ids: set[str] = set()
    for f in fields:
        synth_value = _try_classifier_resolve(f, profile=profile, track=track)
        log.info(
            "pre-resolve attempt: id=%s label=%r synth=%r kind=%s",
            f.element_id, (f.label or "")[:60],
            (synth_value or "")[:60], f.kind,
        )
        if synth_value:
            ok = _fill_one(page, f, synth_value)
            log.info(
                "pre-resolve fill result: id=%s filled=%s", f.element_id, ok,
            )
            audit["per_field"].append({
                "field_id": f.element_id,
                "label": f.label,
                "value": synth_value[:120],
                "source": "classifier+profile",
                "confidence": 1.0,
                "reasoning": "resolved from profile pre-LLM",
                "filled": ok,
            })
            if ok:
                audit["filled_count"] += 1
                preresolved_ids.add(f.element_id)
                log.info(
                    "dom_batch: pre-resolved %s (%r) ← %r [classifier+profile]",
                    f.element_id, (f.label or "")[:50], synth_value[:60],
                )
                # Close any lingering dropdown state so the next fill
                # doesn't inherit it.
                _close_dropdown_state(page)

    # Re-scan the DOM: filling some fields (especially cascading EEO
    # selects) may have revealed new required fields that weren't
    # visible / required at the initial scrape. This catches the Axon
    # ``Please identify your race`` field, which is rendered only
    # after Gender has a value. Any newly-discovered required fields
    # go through the full pre-resolve + LLM pipeline below.
    try:
        new_fields = collect_empty_required_fields(page, already_filled_keys)
    except Exception as exc:
        log.debug("dom_batch: rescan failed: %s", exc)
        new_fields = []
    already_seen_ids = {f.element_id for f in fields}
    late_fields = [f for f in new_fields if f.element_id not in already_seen_ids]
    if late_fields:
        log.info(
            "dom_batch: rescan found %d new required field(s): %s",
            len(late_fields),
            [f.element_id for f in late_fields][:10],
        )
        # Scrape options for the late-discovered select fields.
        for f in late_fields:
            if f.kind != "select":
                continue
            f.options = _scrape_select_options(page, f.locator)
            if not f.options:
                try:
                    classified = _classify_for_seed(f.label)
                except Exception:
                    classified = None
                seed = ""
                if classified is not None:
                    if classified.type is _QT.SCHOOL:
                        seed = "university of mary"
                    elif classified.type is _QT.MAJOR:
                        seed = "computer"
                    elif classified.type is _QT.DEGREE:
                        seed = "bachelor"
                if seed:
                    f.options = _scrape_async_typeahead_options(
                        page, f.locator, seed,
                    )
                if not f.options:
                    f.kind = "text"
        # Pre-resolve the late fields too.
        for f in late_fields:
            synth_value = _try_classifier_resolve(
                f, profile=profile, track=track,
            )
            if synth_value:
                ok = _fill_one(page, f, synth_value)
                audit["per_field"].append({
                    "field_id": f.element_id,
                    "label": f.label,
                    "value": synth_value[:120],
                    "source": "classifier+profile (late)",
                    "confidence": 1.0,
                    "reasoning": "resolved from profile pre-LLM, late scan",
                    "filled": ok,
                })
                if ok:
                    audit["filled_count"] += 1
                    preresolved_ids.add(f.element_id)
                    log.info(
                        "dom_batch: late pre-resolved %s (%r) ← %r",
                        f.element_id, (f.label or "")[:50], synth_value[:60],
                    )
                    _close_dropdown_state(page)
        # Merge late fields into the set that gets sent to the LLM.
        fields = fields + late_fields

    # Only send remaining fields to the LLM.
    remaining = [f for f in fields if f.element_id not in preresolved_ids]
    if not remaining:
        return audit

    batch_qs: list[BatchQuestion] = []
    for f in remaining:
        batch_qs.append(BatchQuestion(
            id=f.element_id,
            label=f.label,
            kind=f.kind,
            required=True,
            options=list(f.options),
        ))

    # One LLM call.
    result = resolve_batch(
        questions=batch_qs,
        profile=profile,
        answer_bank_yaml=answer_bank_yaml,
        track=track,
        company=company,
        job_title=job_title,
    )

    audit["model_used"] = result.model_used
    audit["cascade_trace"] = result.cascade_trace
    audit["error"] = result.error

    if result.error or not result.answers:
        log.warning("dom_batch: batch resolution failed: %s", result.error)
        return audit

    # Fill each answered field.
    field_by_id = {f.element_id: f for f in fields}
    for qid, answer in result.answers.items():
        f = field_by_id.get(qid)
        if f is None:
            continue
        if answer.source == "needs_review" or answer.value is None:
            audit["per_field"].append({
                "field_id": qid,
                "label": f.label,
                "value": None,
                "source": answer.source,
                "confidence": answer.confidence,
                "reasoning": answer.reasoning,
                "filled": False,
            })
            continue

        # Multi-select → join to comma-separated; caller's fill logic
        # handles the breakdown.
        if isinstance(answer.value, list):
            val = ", ".join(str(v) for v in answer.value)
        else:
            val = str(answer.value)

        filled_ok = _fill_one(page, f, val)
        audit["per_field"].append({
            "field_id": qid,
            "label": f.label,
            "value": val[:120],
            "source": answer.source,
            "confidence": answer.confidence,
            "reasoning": answer.reasoning,
            "filled": filled_ok,
        })
        if filled_ok:
            audit["filled_count"] += 1
            log.info(
                "dom_batch: filled %s (%r) ← %r [%s]",
                qid, (f.label or "")[:50], val[:60], answer.source,
            )
        # Always commit + blur between fills so the React-Select
        # reconciler finishes writing the selection before the next
        # field's mousedown fires. Without this, observations on Axon
        # EEO showed Gender committing but the subsequent Hispanic
        # fill's events arrived while Gender was still propagating,
        # and the net state had Hispanic committed but Gender's
        # commit lost.
        _close_dropdown_state(page)

    return audit


# ─── Education-field option preferences ──────────────────────────────────
#
# Ordered preference regexes for school / degree / discipline dropdowns,
# loaded from state/rules/education_preferences.yml. See that file's
# header for semantics and update-policy; each list is an ORDERED
# preference — earlier patterns win.

_EDU_PREFS = load_rules("education_preferences")
_SCHOOL_OPTION_PREFERENCES: tuple[str, ...] = tuple(_EDU_PREFS["school"])
_DEGREE_OPTION_PREFERENCES: tuple[str, ...] = tuple(_EDU_PREFS["degree"])
_DISCIPLINE_OPTION_PREFERENCES: tuple[str, ...] = tuple(_EDU_PREFS["discipline"])

# US states + DC + territories, lowercase. Used by the checkbox
# pre-resolve to auto-check Maryland on multi-state "which locations
# are you 100% committed to" grids. Loaded from state/rules/geography.yml.
_US_STATE_LABELS: frozenset[str] = frozenset(
    load_rules("geography")["us_state_labels"]
)


def _match_preferred_option(
    options: list[str], preferences: tuple[str, ...]
) -> str | None:
    """Return the first option text matching the highest-priority regex.

    Iterates the preference regexes in order; for each, scans the option
    list for a match (case-insensitive). Returns the exact option text
    so the caller can pass it verbatim to ``fill_combobox`` / ``fill_select``.
    """
    if not options or not preferences:
        return None
    import re as _re
    for pat in preferences:
        rx = _re.compile(pat, _re.IGNORECASE)
        for opt in options:
            if rx.search(opt or ""):
                return opt
    return None


def _try_classifier_resolve(
    f: _DomField, *, profile: "Profile", track: str,
) -> str | None:
    """Resolve a scraped DOM field via the deterministic classifier
    pipeline before falling back to the LLM batch.

    Catches fields whose label the classifier already knows how to
    map (``SCHOOL``, ``DEGREE``, ``MAJOR``, ``GRADUATION_DATE``,
    demographic fields, ...) so we don't waste LLM tokens on answers
    the profile already provides verbatim.

    Returns the resolved value string, or ``None`` if:
      * classifier returns ``UNKNOWN``;
      * the bank / profile has no value for the type;
      * the field is a ``select`` whose resolved value doesn't match
        any scraped option (in which case the LLM is a better judge
        of which option to pick).
    """
    from autoapply.answers.classifier import classify as _classify
    from autoapply.answers.bank import AnswerBank
    from autoapply.answers.types import QuestionType
    from autoapply.config import get_settings

    # Checkbox shortcut: state-grid and similar lists have per-option
    # checkboxes whose labels are bare state names ("Alabama", "Alaska",
    # ..., "Maryland", ...). The classifier returns UNKNOWN for these
    # (they're not questions, they're option labels). Intercept
    # before classify() so we can auto-resolve based on label alone.
    if f.kind == "checkbox":
        lbl_lower = (f.label or "").strip().lower()
        if lbl_lower in _US_STATE_LABELS:
            return "Yes" if lbl_lower == "maryland" else "No"

    try:
        classified = _classify(f.label)
    except Exception:
        return None
    if classified.type is QuestionType.UNKNOWN:
        log.debug(
            "pre-resolve: label=%r → classifier UNKNOWN; deferring to LLM",
            f.label[:80],
        )
        return None
    log.debug(
        "pre-resolve: label=%r → classifier=%s",
        f.label[:80], classified.type.value,
    )

    # Lazily load the bank — matches the pattern in llm_batch.
    try:
        bank = AnswerBank.from_path(get_settings().answer_bank_path)
    except Exception:
        return None

    try:
        ans = bank.answer(classified, profile=profile, track=track)
    except Exception as exc:
        log.debug(
            "pre-resolve: bank.answer raised for label=%r: %s",
            f.label[:60], exc,
        )
        return None

    value = ans.value
    log.debug(
        "pre-resolve: bank.answer label=%r value=%r review=%s llm=%s",
        f.label[:60], (value or "")[:60],
        ans.requires_review, ans.requires_llm,
    )
    if not value:
        return None

    # School-specific: profile stores the generic "University of
    # Maryland" but UMD has multiple campuses (College Park,
    # Baltimore, Eastern Shore). Typing the full "College Park" form
    # into async-typeahead School dropdowns returns zero options on
    # many backends (they expect a substring of an exact option).
    # So we keep ``value`` as-is (short profile form) and instead
    # rely on the preference-matcher path in
    # :func:`_match_preferred_option` above, which picks the correct
    # College Park variant from the scraped options list when one
    # exists. When no variant is scraped (partial options list),
    # fill_combobox types the bank value as-is and picks the first
    # alphabetical match. That means we may submit with "Baltimore"
    # on some tenants — an accepted but imprecise answer — rather
    # than blocking submission entirely.

    # Checkbox-specific: if the field is a standalone checkbox and its
    # label is a US state / territory name, treat it like a per-state
    # selector. Check the state when it's in the candidate's
    # ``willing_to_work_states`` list (defaults to all 50 + DC). This
    # handles the mthree "We hire in multiple locations; please select
    # which you're 100% committed to working in" pattern, where each
    # state is rendered as its own checkbox with only the state name
    # as the label.
    if f.kind == "checkbox":
        lbl_lower = (f.label or "").strip().lower()
        if lbl_lower in _US_STATE_LABELS:
            willing = getattr(profile, "willing_to_work_states", None) or []
            willing_lower = {s.strip().lower() for s in willing}
            return "Yes" if lbl_lower in willing_lower else "No"

    # Education fields get an extra pass: match scraped options against
    # an ORDERED preference list so we always pick the canonical option
    # (e.g., "Bachelor of Science" before "B.S." before "Bachelor's
    # Degree"). This guarantees a verbatim option match that
    # fill_combobox can commit deterministically — no typing, no
    # filter race, no LLM token usage.
    if f.kind == "select" and f.options:
        prefs: tuple[str, ...] | None = None
        if classified.type is QuestionType.SCHOOL:
            prefs = _SCHOOL_OPTION_PREFERENCES
        elif classified.type is QuestionType.DEGREE:
            prefs = _DEGREE_OPTION_PREFERENCES
        elif classified.type is QuestionType.MAJOR:
            prefs = _DISCIPLINE_OPTION_PREFERENCES
        if prefs:
            preferred = _match_preferred_option(f.options, prefs)
            if preferred is not None:
                log.info(
                    "pre-resolve: preference match for %s → %r",
                    classified.type.value, preferred[:60],
                )
                return preferred
        # No preference hit — fall through to canonical-form match
        # below. For education fields, this covers tenants whose
        # option list doesn't contain any of our preferred phrasings.

    # For select fields with scraped options, try to canonicalize the
    # value to match an existing option (case-insensitive exact match).
    # If we can't canonicalize, STILL return the bank value — fill_combobox
    # will type it and rely on React-Select's internal filter + async
    # typeahead loading to find a matching option. This handles:
    #   * Schools (10k+ entries, only some shown on open)
    #   * Countries (240 entries, fully shown but may not exact-match)
    #   * Degrees ("B.S. Computer Science, Mathematics" vs option
    #             "Bachelor of Science" — typed, then async filter picks)
    if f.kind == "select" and f.options:
        v_lo = value.strip().lower()
        canonical = next(
            (o for o in f.options if o.strip().lower() == v_lo),
            None,
        )
        if canonical is not None:
            return canonical
        # Token-set fallback — punctuation-tolerant ("University of
        # Maryland, College Park" profile → "University of Maryland -
        # College Park" option).
        from .field_fill import _normalize_tokens as _norm_toks
        v_tokens = _norm_toks(value)
        if len(v_tokens) >= 2:
            best_opt: str | None = None
            best_overlap = 0
            for opt in f.options:
                opt_toks = _norm_toks(opt)
                if v_tokens.issubset(opt_toks) or (
                    opt_toks.issubset(v_tokens) and len(opt_toks) >= 2
                ):
                    if len(opt_toks) > best_overlap:
                        best_overlap = len(opt_toks)
                        best_opt = opt
            if best_opt is not None:
                log.info(
                    "pre-resolve: token-set match %r → %r",
                    value[:40], best_opt[:60],
                )
                return best_opt
        # Fall through — return the bank value as-is; fill_combobox
        # will type it and rely on React-Select's async filter.

    return value


def _close_dropdown_state(page: Any) -> None:
    """Blur any focused control before the next fill's mousedown fires.

    Strategy: call ``document.activeElement.blur()`` directly — this
    dispatches the React-Select component's onBlur which
    finalizes/commits any pending selection. We avoid:
      * Tab — moves focus to the next focusable element, which may
        itself be a React-Select input and trigger its own events.
      * Escape — on some React-Select versions cancels the last pick.
      * Mouse click elsewhere — lands on a real element and can
        trigger unintended handlers.

    A direct JS blur call is the safest "commit and clear focus"
    operation that doesn't interact with anything else on the page.
    """
    try:
        page.evaluate("""() => {
            const el = document.activeElement;
            if (el && el.blur) el.blur();
        }""")
    except Exception:
        pass
    jitter(0.3, 0.5)


def _education_patterns_for_label(label: str) -> tuple[str, ...] | None:
    """Map a scraped DOM label to preferred-option regexes for education fields.

    Returns the ordered pattern tuple (``fill_combobox`` tries them in
    order; first option matching highest-priority pattern wins), or None
    if the label isn't an education field.

    Used to disambiguate multi-option matches when the typed value
    (e.g. "University of Maryland") matches many options. Without
    preferences, fill_combobox picks alphabetically-first ("Baltimore").
    With SCHOOL preferences, it picks "College Park" (Aadit's actual
    campus) every time.
    """
    lo = (label or "").strip().lower()
    if "school" in lo or "university" in lo or "college" in lo:
        return _SCHOOL_OPTION_PREFERENCES
    if "degree" in lo and "discipline" not in lo:
        return _DEGREE_OPTION_PREFERENCES
    if (
        "discipline" in lo
        or "major" in lo
        or "field of study" in lo
        or "concentration" in lo
    ):
        return _DISCIPLINE_OPTION_PREFERENCES
    return None


def _fill_one(page: Any, f: _DomField, value: str) -> bool:
    """Fill one scraped field with the LLM-returned value.

    Dispatch:
      * kind="checkbox"   → check() or uncheck() based on truthy value
      * native <select>   → ``fill_select``
      * React-Select      → ``fill_combobox`` (with prefer_patterns for
                             education fields — School/Degree/Major)
      * text / textarea   → ``.fill()`` + React-Select verify-and-retry

    For checkboxes, ``value`` is interpreted as a boolean-like token —
    "yes"/"true"/"1"/"agree"/"i agree"/"accept"/"i accept"/"confirm"/
    "i consent" → check; anything else → uncheck. Standalone required
    consent boxes (T&C, GDPR, privacy policy) should always be checked
    once the candidate has reviewed them.

    Critical: we **re-locate** the element fresh from the DOM rather than
    using ``f.locator`` from the initial scrape. React-Select widgets
    unmount-and-remount when adjacent fields commit (observed on
    jjsnackfoods: filling Degree re-rendered the whole education
    group, leaving Discipline's captured locator pointing at a
    detached DOM node — subsequent click/type fell through to the
    next form input, LinkedIn Profile). Re-locating by ``id`` /
    ``name`` guarantees a live handle.

    Returns True on apparent success; False if every strategy raised.
    """
    # Import here — field_fill imports llm_fallback at module load
    # and we want to keep dom_batch's import graph simple.
    from .field_fill import _is_react_select, fill_combobox, fill_select

    # Re-resolve a fresh locator by id/name. Falls back to the original
    # locator only if the re-resolution finds nothing (e.g., the field
    # truly disappeared from the DOM between scrape and fill).
    fresh_el = None
    try:
        by_id = page.locator(f'[id="{f.element_id}"]')
        if by_id.count() > 0:
            fresh_el = by_id.first
        else:
            by_name = page.locator(f'[name="{f.element_id}"]')
            if by_name.count() > 0:
                fresh_el = by_name.first
    except Exception:
        pass
    el = fresh_el if fresh_el is not None else f.locator

    try:
        tag = el.evaluate("e => e.tagName.toLowerCase()") or ""
    except Exception:
        tag = ""

    try:
        if f.kind == "checkbox":
            _YES_TOKENS = {
                "yes", "y", "true", "1", "agree", "i agree", "accept",
                "i accept", "consent", "i consent", "confirm", "i confirm",
                "on", "checked",
            }
            v = (value or "").strip().lower()
            truthy = v in _YES_TOKENS or v.startswith("yes")
            try:
                el.scroll_into_view_if_needed(timeout=1_500)
            except Exception:
                pass
            if truthy:
                el.check(timeout=2_500, force=True)
            else:
                el.uncheck(timeout=2_500, force=True)
            return True
        # Education fields (School / Degree / Major / Discipline) get
        # preferred-pattern lists so fill_combobox picks the College
        # Park UMD campus (not Baltimore) and the correct degree/major
        # canonical form across tenants that list multiple synonyms.
        prefer_patterns = _education_patterns_for_label(f.label)

        if tag == "select":
            fill_select(el, value)
            return True
        if _is_react_select(el):
            fill_combobox(page, el, value, prefer_patterns=prefer_patterns)
            return True
        # Plain text fallback.
        try:
            el.fill(value, timeout=3_000)
            actual = (el.input_value(timeout=500) or "").strip()
            if not actual:
                fill_combobox(page, el, value, prefer_patterns=prefer_patterns)
        except Exception:
            fill_combobox(page, el, value, prefer_patterns=prefer_patterns)
        return True
    except Exception as exc:
        log.debug(
            "dom_batch: fill failed for %s (%r): %s",
            f.element_id, (f.label or "")[:50], exc,
        )
        return False
