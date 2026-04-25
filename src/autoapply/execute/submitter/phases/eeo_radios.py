"""EEO radio-group filler — DOM-only path.

Greenhouse and Lever expose EEO questions through their form-spec
APIs, so they get filled via ``fill_api_fields`` (or
``fill_lever_cards``). Ashby's hosted SPA does NOT expose form
metadata to third parties — we discover EEO controls by walking the
DOM at submit time. Each EEO question is rendered as a radio group
with a UUID-based ``name`` attribute (no semantic info), so we have
to figure out what each group MEANS by reading the group's question
heading.

Strategy per radio group:

1. Group radios by their ``name`` attribute.
2. Skip groups that already have a checked option (filled by
   Ashby's autofill or a prior pass).
3. Find the group's question heading by walking ancestors looking
   for a ``<fieldset legend>``, ``<label>``-near-radios, or any
   container element whose visible text matches a known EEO
   QuestionType regex via the classifier.
4. Map the classified ``QuestionType`` to a profile attribute
   (gender, race, hispanic, veteran, disability).
5. Find the radio in the group whose label text matches the profile
   value — falling back to "decline / prefer not to answer" if the
   profile says decline. Reuses
   ``state/rules/eeo_semantics.yml::decline_keywords``.
6. Click it.

Conservative on purpose: only fires on Ashby URLs, only fills the
five well-known EEO question types, and never overwrites a checked
radio. Anything ambiguous is left alone — the form may still have
optional EEO blocks that Ashby's risk engine is happy to receive
empty.
"""

from __future__ import annotations

import logging
from typing import Any

from autoapply.answers.classifier import classify
from autoapply.answers.types import QuestionType
from autoapply.rules import load_rules


log = logging.getLogger(__name__)


# Profile attributes per EEO QuestionType. Mirrors the schema defaults
# in ``profile/schema.py::Profile`` (demo_gender, demo_race, etc.).
_TYPE_TO_PROFILE_ATTR: dict[QuestionType, str] = {
    QuestionType.DEMO_GENDER: "demo_gender",
    QuestionType.DEMO_RACE: "demo_race",
    QuestionType.DEMO_HISPANIC_LATINO: "demo_hispanic_latino",
    QuestionType.DEMO_VETERAN: "demo_veteran",
    QuestionType.DEMO_DISABILITY: "demo_disability",
}


def fill_eeo_radios(page: Any, profile: Any, field_errors: list[str]) -> None:
    """Walk radio groups and check the option matching the profile EEO value.

    No-op when ``profile`` is None (some test paths don't construct one).
    Per-group failures are appended to ``field_errors`` as
    ``eeo_radio:<reason>`` strings; non-fatal.
    """
    if profile is None:
        return

    try:
        decline_phrases = tuple(
            str(p).lower() for p in load_rules("eeo_semantics").get("decline_keywords") or []
        )
    except Exception:
        decline_phrases = ("decline", "prefer not", "not wish", "do not wish")

    try:
        groups = _collect_radio_groups(page)
    except Exception as exc:
        log.debug("eeo_radios: enumerate failed — %s", exc)
        return

    for name, radios in groups.items():
        try:
            # Skip groups where any option is already checked (autofill,
            # earlier phase, or a prior fill_eeo_radios pass on retry).
            if any(_is_checked(r) for r in radios):
                continue

            heading_text = _group_heading(page, radios)
            if not heading_text:
                log.debug(
                    "eeo_radios: name=%s — no heading found, skipping", name,
                )
                continue

            classified = classify(heading_text)
            qt = classified.type

            # OFCCP combined race+ethnicity questions lead with text
            # like ``"Hispanic or Latino"`` (the first option's label
            # if our heading heuristic grabbed it) — the classifier
            # returns DEMO_HISPANIC_LATINO. But a Yes/No Hispanic
            # question has 2-3 options at most; >3 options means this
            # is the race rollup. Re-classify so we use ``demo_race``.
            if (
                qt is QuestionType.DEMO_HISPANIC_LATINO
                and len(radios) > 3
            ):
                log.info(
                    "eeo_radios: name=%s — heading=%r looked Hispanic but "
                    "%d options → treating as DEMO_RACE",
                    name[:40], heading_text[:60], len(radios),
                )
                qt = QuestionType.DEMO_RACE

            attr = _TYPE_TO_PROFILE_ATTR.get(qt)
            if attr is None:
                # Not an EEO question we know how to answer. Skip
                # rather than guess — leaving an unknown radio
                # group unchecked is safer than wrong-checking.
                log.info(
                    "eeo_radios: name=%s — heading=%r classified as %s (not EEO); skipping",
                    name[:40], heading_text[:80], qt.value,
                )
                continue

            target_value = str(getattr(profile, attr, "") or "").strip()
            if not target_value:
                continue

            picked = _pick_option(
                page, radios,
                target_value=target_value,
                decline_phrases=decline_phrases,
            )
            if picked is None:
                # Capture the actual label text we considered, so
                # next iteration of the picker can be tuned to the
                # specific phrasing this tenant uses.
                seen_labels = _snapshot_labels(page, radios)
                log.info(
                    "eeo_radios: name=%s — no option matched %s=%r "
                    "(heading=%r, labels=%r)",
                    name[:40], attr, target_value,
                    heading_text[:60],
                    [l[:60] for l in seen_labels[:6]],
                )
                continue

            try:
                picked.check(timeout=3_000, force=True)
                log.info(
                    "eeo_radios: filled %s ← %r (heading=%r)",
                    qt.value, target_value, heading_text[:60],
                )
            except Exception as exc:
                log.debug(
                    "eeo_radios: check failed for name=%s: %s", name, exc,
                )
                field_errors.append(f"eeo_radio:{type(exc).__name__}")
        except Exception as exc:
            log.debug("eeo_radios: group %s failed — %s", name, exc)
            field_errors.append(f"eeo_radio:{type(exc).__name__}")


# ── internals ────────────────────────────────────────────────────────────


def _collect_radio_groups(page: Any) -> dict[str, list[Any]]:
    """Return ``{name_attr: [radio elements...]}``.

    Plays it safe — ignores radios without a ``name`` attribute, since
    those can't be grouped reliably.
    """
    out: dict[str, list[Any]] = {}
    try:
        radios = page.locator('input[type="radio"]').all()
    except Exception:
        return out
    for r in radios:
        try:
            n = r.get_attribute("name") or ""
            if not n:
                continue
            out.setdefault(n, []).append(r)
        except Exception:
            continue
    return out


def _is_checked(radio: Any) -> bool:
    try:
        return bool(radio.evaluate("e => e.checked"))
    except Exception:
        return False


def _snapshot_labels(page: Any, radios: list[Any]) -> list[str]:
    """Read the visible label text for each radio in a group.

    Same lookup ladder ``_pick_option`` uses internally — surfaced as
    a public helper so callers can log the labels we considered when
    no option was matched.
    """
    out: list[str] = []
    for r in radios:
        try:
            rid = r.get_attribute("id") or ""
            if rid:
                lbl = page.locator(f'label[for="{rid}"]')
                if lbl.count() > 0:
                    out.append((lbl.first.inner_text() or "").strip())
                    continue
            anc = r.locator("xpath=ancestor::label[1]")
            if anc.count() > 0:
                out.append((anc.first.inner_text() or "").strip())
                continue
            al = (r.get_attribute("aria-label") or "").strip()
            if al:
                out.append(al)
                continue
            out.append((r.get_attribute("value") or "").strip())
        except Exception:
            out.append("")
    return out


def _group_heading(page: Any, radios: list[Any]) -> str:
    """Best-effort find the EEO question prompt for a radio group.

    Tries (in order):
      1. ``<legend>`` inside the closest ``<fieldset>`` ancestor.
      2. ``aria-labelledby`` on the first radio → referenced element text.
      3. ``role="group"`` ancestor's ``aria-label``.
      4. Inner text of the closest ancestor whose first visible text
         is plausibly a question (length-bounded, ends with ``?`` or
         contains a known EEO keyword).
    """
    if not radios:
        return ""
    first = radios[0]

    # 1. <legend> ancestor
    try:
        leg = first.locator("xpath=ancestor::fieldset[1]/legend")
        if leg.count() > 0:
            t = (leg.first.inner_text() or "").strip()
            if t:
                return t
    except Exception:
        pass

    # 2. aria-labelledby
    try:
        alb = (first.get_attribute("aria-labelledby") or "").strip()
        if alb:
            for rid in alb.split():
                loc = page.locator(f'[id="{rid}"]')
                if loc.count() > 0:
                    t = (loc.first.inner_text() or "").strip()
                    if t:
                        return t
    except Exception:
        pass

    # 3. role=group ancestor with aria-label
    try:
        anc = first.locator(
            "xpath=ancestor::*[@role='group' or @role='radiogroup'][1]"
        )
        if anc.count() > 0:
            al = (anc.first.get_attribute("aria-label") or "").strip()
            if al:
                return al
    except Exception:
        pass

    # 4. Container text heuristic — climb a few levels and grab the
    # heading-like first paragraph. Capped to keep noise out.
    try:
        txt = first.evaluate(
            """(el) => {
                let cur = el.parentElement;
                for (let depth = 0; depth < 6 && cur; depth++) {
                    const t = (cur.innerText || '').trim();
                    if (t && t.length < 220) {
                        // First non-empty line is usually the heading
                        const head = t.split(/\\n+/)[0].trim();
                        if (head.length > 4 && head.length < 200) return head;
                    }
                    cur = cur.parentElement;
                }
                return '';
            }"""
        )
        if isinstance(txt, str) and txt:
            return txt
    except Exception:
        pass

    return ""


def _pick_option(
    page: Any,
    radios: list[Any],
    *,
    target_value: str,
    decline_phrases: tuple[str, ...],
) -> Any:
    """Return the radio whose label matches the profile value, or None.

    Three-pass match:
      1. Exact case-insensitive label match.
      2. Substring match (label contains target_value tokens).
      3. Decline-fallback — when target_value is a decline-style
         phrase ("Decline to self-identify", "I do not wish to answer"),
         pick any radio whose label contains a decline keyword.
    """
    target_lo = target_value.lower().strip()
    is_decline = any(p in target_lo for p in decline_phrases)

    # Snapshot label texts once.
    snaps: list[tuple[Any, str]] = []
    for r in radios:
        try:
            rid = r.get_attribute("id") or ""
            if rid:
                lbl = page.locator(f'label[for="{rid}"]')
                if lbl.count() > 0:
                    snaps.append((r, (lbl.first.inner_text() or "").strip().lower()))
                    continue
            # Ancestor label
            anc = r.locator("xpath=ancestor::label[1]")
            if anc.count() > 0:
                snaps.append((r, (anc.first.inner_text() or "").strip().lower()))
                continue
            # aria-label / value attr
            al = (r.get_attribute("aria-label") or "").strip().lower()
            if al:
                snaps.append((r, al))
                continue
            v = (r.get_attribute("value") or "").strip().lower()
            snaps.append((r, v))
        except Exception:
            snaps.append((r, ""))

    # Pass 1 — exact match.
    for r, lbl in snaps:
        if lbl == target_lo:
            return r

    # Pass 2a — short Yes/No targets need word-boundary handling.
    # Profile values like ``demo_hispanic_latino="No"`` should match
    # an option label that's:
    #   * ``"No, I am not Hispanic or Latino"`` (leading "No,") OR
    #   * ``"Not Hispanic or Latino"``         (negation, no "No,") OR
    #   * ``"I am not Hispanic or Latino"``    (sentence form).
    # Same for "Yes" → labels starting with "Yes," / "I am ".
    if target_lo in ("yes", "no") and " " not in target_lo:
        import re as _re
        # Direct anchored match (label starts with the target word).
        anchored = _re.compile(rf"^\s*{_re.escape(target_lo)}\b")
        for r, lbl in snaps:
            if anchored.match(lbl):
                return r
        # Negation / affirmation phrasing fallback.
        if target_lo == "no":
            neg = _re.compile(
                r"^\s*(?:not\s+|"           # "Not Hispanic or Latino"
                r"i\s+am\s+not\s+|"          # "I am not Hispanic or Latino"
                r"i\s+do\s+not\s+|"          # "I do not have a disability"
                r"i\s+don'?t\s+|"            # "I don't have ..."
                r"do\s+not\s+|"
                r"don'?t\s+)"
            )
            for r, lbl in snaps:
                if neg.match(lbl):
                    return r
        else:  # yes
            aff = _re.compile(
                r"^\s*(?:i\s+am\s+|"          # "I am Hispanic or Latino"
                r"i\s+do\s+|"                  # "I do have ..."
                r"i\s+have\s+)"                # "I have a disability ..."
            )
            for r, lbl in snaps:
                if aff.match(lbl):
                    return r

    # Pass 2b — substring (multi-char target tokens all present in label).
    target_tokens = [t for t in target_lo.split() if len(t) > 2]
    if target_tokens:
        for r, lbl in snaps:
            if all(tok in lbl for tok in target_tokens):
                return r

    # Pass 3 — decline fallback.
    if is_decline:
        for r, lbl in snaps:
            if any(p in lbl for p in decline_phrases):
                return r

    return None
