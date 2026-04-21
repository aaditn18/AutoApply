"""Pre-resolve a scraped DOM field via classifier + profile + bank.

Before sending a field to the batched LLM, we try the deterministic
classifier + profile + bank pipeline. This catches fields whose labels
the classifier already knows — ``SCHOOL``, ``DEGREE``, ``MAJOR``,
``GRADUATION_DATE``, demographics — so we don't pay LLM tokens for
answers the profile already provides verbatim.

Returns the resolved string, or ``None`` when:

- the classifier returns ``UNKNOWN``;
- the bank / profile has no value for the type;
- (for text fields) we'd need to guess.

Special cases:

- **Checkbox labels that ARE state names** (Alabama, Maryland, ...)
  — classifier returns UNKNOWN for these (they're not questions),
  so we short-circuit on the ``_US_STATE_LABELS`` set and return
  Yes/No based on ``profile.willing_to_work_states``.
- **Education fields with scraped options** — run the option text
  through :func:`_match_preferred_option` first so we pick the exact
  variant (e.g. "College Park" before "Baltimore County") without
  touching the LLM.
- **Select with scraped options** — try exact match then token-set
  overlap before returning the bank value as-is (``fill_combobox``
  types and picks from async-loaded options).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from autoapply.answers.types import QuestionType

from .fields import _DomField
from .preferences import (
    _DEGREE_OPTION_PREFERENCES,
    _DISCIPLINE_OPTION_PREFERENCES,
    _SCHOOL_OPTION_PREFERENCES,
    _US_STATE_LABELS,
    _match_preferred_option,
)


if TYPE_CHECKING:
    from autoapply.profile.schema import Profile


log = logging.getLogger(__name__)


def _try_classifier_resolve(
    f: _DomField, *, profile: "Profile", track: str,
) -> str | None:
    """Resolve a scraped DOM field via the deterministic pipeline.

    Returns the resolved value string, or ``None`` if the field should
    go to the LLM batch instead.
    """
    from autoapply.answers.classifier import classify as _classify
    from autoapply.answers.bank import AnswerBank
    from autoapply.config import get_settings

    # Checkbox shortcut: state-grid and similar lists have per-option
    # checkboxes whose labels are bare state names ("Alabama", "Alaska",
    # ..., "Maryland", ...). The classifier returns UNKNOWN for these
    # (they're not questions, they're option labels). Intercept before
    # classify() so we can auto-resolve based on label alone.
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
    # many backends (they expect a substring of an exact option). So
    # we keep ``value`` as-is (short profile form) and instead rely on
    # the preference-matcher path in :func:`_match_preferred_option`,
    # which picks the correct College Park variant from the scraped
    # options list when one exists. When no variant is scraped
    # (partial options list), fill_combobox types the bank value as-is
    # and picks the first alphabetical match. That means we may submit
    # with "Baltimore" on some tenants — an accepted but imprecise
    # answer — rather than blocking submission entirely.

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
    # If we can't canonicalize, STILL return the bank value —
    # fill_combobox will type it and rely on React-Select's internal
    # filter + async typeahead loading to find a matching option.
    # This handles:
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
        from ..field_fill import _normalize_tokens as _norm_toks

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
        # Type-mismatch defer: the bank returned a numeric value
        # (e.g. GPA "3.975") but the options are all non-numeric
        # (e.g. ["Yes", "No"] for a threshold question). Typing
        # "3.975" into a Yes/No React-Select would fail validation.
        # Defer to the batch LLM, which has the threshold rule in
        # ``prompts/batch_rules.md`` and can compare the profile
        # number to the threshold in the label. Non-numeric mismatches
        # (e.g. profile school "University of Maryland - College Park"
        # vs an async-typeahead options list) still fall through to
        # ``fill_combobox`` which types-and-filters.
        import re as _re
        if _re.fullmatch(r"\d+(?:\.\d+)?", value.strip()) and not any(
            _re.search(r"\d", opt or "") for opt in f.options
        ):
            log.info(
                "pre-resolve: numeric %r vs non-numeric options %s; deferring to LLM",
                value[:20], [o[:20] for o in f.options[:4]],
            )
            return None
        # Fall through — return the bank value as-is; fill_combobox
        # will type it and rely on React-Select's async filter.

    return value
