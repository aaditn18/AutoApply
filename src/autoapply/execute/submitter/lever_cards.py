"""Lever qualifying-question card resolver.

Lever's public posting API often returns ``customQuestions: []`` even when
the apply page renders custom questions via their SPA. Those DOM fields are
identified by ``name="cards[UUID][fieldN]"``. This module discovers them at
Playwright time and fills them via heuristic matching against the question
text.
"""

from __future__ import annotations

import logging
from typing import Any

from .field_fill import fill_field
from .util import jitter


log = logging.getLogger(__name__)


def fill_lever_cards(
    page: Any, already_filled_names: set[str], field_errors: list[str]
) -> None:
    """Discover and fill Lever qualifying-question card fields at Playwright time.

    Strategy:
    1. Find every unique ``cards[...]`` name that is NOT already in our
       pre-resolved data (``already_filled_names``).
    2. Extract the question text from the ``<li class="application-question">``
       parent — specifically the ``.application-label .text`` inner text.
    3. Apply rule-based heuristics (via :func:`card_heuristic_answer`) to
       determine the answer. Unknown questions are left unfilled and logged
       as ``card_unknown:<name>``.
    4. Fill radio / select fields using :func:`field_fill.fill_field`.
    """
    try:
        # Build a map from card field name → question text using DOM.
        card_info: dict[str, dict] = page.evaluate(
            """() => {
                const result = {};
                const seenNames = new Set();
                document.querySelectorAll('[name]').forEach(el => {
                    const name = el.getAttribute('name');
                    if (!name || !name.startsWith('cards[')) return;
                    if (el.getAttribute('type') === 'hidden') return;
                    if (seenNames.has(name)) return;
                    seenNames.add(name);

                    const tag = el.tagName.toLowerCase();
                    const type = el.getAttribute('type') || tag;

                    // Walk up to <li class="application-question">
                    let node = el;
                    let questionText = '';
                    while (node && node.parentElement) {
                        node = node.parentElement;
                        if (node.classList && node.classList.contains('application-question')) {
                            const textEl = node.querySelector('.application-label .text');
                            if (textEl) {
                                // Clone and strip the required asterisk span
                                const clone = textEl.cloneNode(true);
                                clone.querySelectorAll('span.required').forEach(s => s.remove());
                                questionText = clone.textContent.trim();
                            }
                            break;
                        }
                    }

                    let options = [];
                    if (tag === 'select') {
                        options = Array.from(el.querySelectorAll('option'))
                                      .map(o => o.textContent.trim())
                                      .filter(o => o && o !== 'Select...');
                    }

                    result[name] = {type, questionText, options};
                });
                return result;
            }"""
        )
    except Exception as exc:
        log.debug("fill_lever_cards: JS evaluation failed: %s", exc)
        return

    for name, info in card_info.items():
        if name in already_filled_names:
            continue

        q_text = info.get("questionText", "")
        q_type = info.get("type", "")
        options = info.get("options", [])

        answer = card_heuristic_answer(q_text, q_type, options)
        if answer is None:
            log.debug("fill_lever_cards: no heuristic answer for %r (q=%r)", name, q_text)
            field_errors.append(f"card_unknown:{name}")
            continue

        try:
            fill_field(page, name, answer)
            log.debug("fill_lever_cards: filled %r=%r (q=%r)", name, answer, q_text)
            jitter(0.05, 0.15)
        except Exception as exc:
            log.debug("fill_lever_cards: fill error for %r: %s", name, exc)
            field_errors.append(f"fill_card:{name}:{type(exc).__name__}")


def card_heuristic_answer(q_text: str, q_type: str, options: list[str]) -> str | None:
    """Return a heuristic answer for a Lever card qualifying question.

    Uses the question text and available options to determine the best answer.
    Returns ``None`` when no confident answer can be produced.
    """
    lo = q_text.lower()

    # ── "Are you a U.S. citizen?" (yes/no) — ALWAYS No for Aadit ───────────
    # MUST come before the "work authorized" branch below: the citizenship
    # question often also contains "US" so broad matching is dangerous. We
    # look for the citizen wording specifically, excluding "eligible to work"
    # / "authorized to work" which are a different question with answer=Yes.
    if "citizen" in lo and not any(
        w in lo for w in ("eligible to work", "authorized to work",
                          "work authorization", "legally authorized")
    ):
        if q_type == "radio":
            for opt in options:
                if opt.strip().lower() == "no":
                    return "No"
        return "No"

    # ── Work authorization (OPT counts as YES) ─────────────────────────────
    if any(w in lo for w in ("eligible to work", "authorized to work",
                              "work authorization", "legally authorized")):
        if q_type == "radio":
            for opt in options:
                if opt.strip().lower() == "yes":
                    return "Yes"
        return "Yes"

    # ── Visa / sponsorship required ─────────────────────────────────────────
    if any(w in lo for w in ("sponsorship", "require.*visa", "visa.*require",
                              "need.*visa", "work.*visa")):
        if q_type == "radio":
            for opt in options:
                if opt.strip().lower() == "no":
                    return "No"
        return "No"

    # ── Security clearance (select) ─────────────────────────────────────────
    if any(w in lo for w in ("clearance", "security clearance", "ts/sci", "top secret")):
        # Prefer the "No clearance" option; fall back to the last option (usually no/none).
        for opt in options:
            opt_lower = opt.lower()
            if any(x in opt_lower for x in ("no clearance", "none", "not current")):
                return opt
        # Last non-empty option is usually the most restrictive / "none" option.
        if options:
            return options[-1]

    # ── Willing to relocate ──────────────────────────────────────────────────
    if any(w in lo for w in ("relocation", "willing to relocate", "open to relocation")):
        return "Yes"

    # ── Remote work preference (select or radio) ─────────────────────────────
    if "remote" in lo and "prefer" in lo:
        for opt in options:
            if "remote" in opt.lower():
                return opt

    # ── Start date ───────────────────────────────────────────────────────────
    if any(w in lo for w in ("start date", "when can you start", "earliest start")):
        return "May 2026"

    # ── How did you hear ──────────────────────────────────────────────────────
    if any(w in lo for w in ("how did you hear", "how did you find", "referral source")):
        if options:
            for opt in options:
                if any(x in opt.lower() for x in ("job board", "linkedin", "online", "internet")):
                    return opt
        return "Online job board"

    # ── Yes/No generic (any remaining required boolean) ──────────────────────
    if q_type == "radio" and set(o.lower() for o in options) == {"yes", "no"}:
        # Default yes for positively-framed questions
        if any(w in lo for w in ("able", "willing", "open to", "have you", "do you")):
            return "Yes"

    return None
