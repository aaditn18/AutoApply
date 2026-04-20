"""Tests for the execute/submitter/fillers/ package.

The fillers package was split from an 889-LOC field_fill.py module.
This file locks down:

1. **Backwards compatibility** — the old ``field_fill`` import paths
   still resolve and point at the SAME function objects the new
   ``fillers`` package exports.
2. **Pure-function behavior** — ``matching`` has no Playwright coupling,
   so we can test it directly.
3. **LLM-fallback de-duplication** — the old ``fill_select`` had an
   inline ``_DECLINE_KEYWORDS`` tuple that duplicated the module-level
   one. This file asserts there's no such duplication anymore.
"""

from __future__ import annotations


# ─── Backwards-compat import shim ────────────────────────────────────────


def test_field_fill_shim_reexports_same_objects():
    """``field_fill.X`` must point at the same objects the new
    ``fillers`` package exports — not a duplicate copy."""
    from autoapply.execute.submitter import field_fill, fillers

    assert fillers.fill_field is field_fill.fill_field
    assert fillers.fill_combobox is field_fill.fill_combobox
    assert fillers.fill_select is field_fill.fill_select
    assert fillers.fill_radio is field_fill.fill_radio
    assert fillers._is_react_select is field_fill._is_react_select
    assert fillers._normalize_tokens is field_fill._normalize_tokens
    assert fillers._looks_like_placeholder is field_fill._looks_like_placeholder


def test_old_import_path_still_works():
    """Legacy callers (e.g. tests/test_answer_bank.py:746 and the
    dom/resolve.py token-set fallback) import _normalize_tokens from
    field_fill. That path must keep working."""
    from autoapply.execute.submitter.field_fill import _normalize_tokens

    assert _normalize_tokens("University of Maryland, College Park") == (
        frozenset({"university", "of", "maryland", "college", "park"})
    )


# ─── Pure-function tests (matching.py) ──────────────────────────────────


def test_normalize_tokens_handles_punctuation():
    from autoapply.execute.submitter.fillers.matching import _normalize_tokens

    # The regression that drove token-set matching: punctuation
    # differences must collapse.
    a = _normalize_tokens("University of Maryland, College Park")
    b = _normalize_tokens("University of Maryland-College Park")
    assert a == b


def test_normalize_tokens_drops_initials():
    from autoapply.execute.submitter.fillers.matching import _normalize_tokens

    # 1-char tokens (initials, noise) are dropped.
    tokens = _normalize_tokens("J. P. Morgan")
    assert tokens == frozenset({"morgan"})


def test_normalize_tokens_empty_input():
    from autoapply.execute.submitter.fillers.matching import _normalize_tokens

    assert _normalize_tokens("") == frozenset()
    assert _normalize_tokens(None) == frozenset()  # type: ignore[arg-type]


def test_looks_like_placeholder_true_cases():
    from autoapply.execute.submitter.fillers.matching import _looks_like_placeholder

    for ph in [
        "",
        "   ",
        "Select",
        "Select...",
        "Select an option",
        "Select one",
        "Choose",
        "Choose...",
        "Please select",
        "--",
        "—",
    ]:
        assert _looks_like_placeholder(ph), f"{ph!r} should be placeholder"


def test_looks_like_placeholder_false_cases():
    from autoapply.execute.submitter.fillers.matching import _looks_like_placeholder

    for real in [
        "Yes",
        "No",
        "Male",
        "University of Maryland",
        "Bachelor of Science",
        "Decline to self-identify",
    ]:
        assert not _looks_like_placeholder(real), f"{real!r} should NOT be placeholder"


# ─── _DECLINE_KEYWORDS de-duplication ───────────────────────────────────


def test_decline_keywords_loaded_from_yaml_once():
    """Regression: the old fill_select had an inline _DECLINE_KEYWORDS
    tuple duplicating the module-level one. After Phase 4, both fillers
    load the SAME tuple from state/rules/eeo_semantics.yml."""
    from autoapply.execute.submitter.fillers.native_select import (
        _DECLINE_KEYWORDS as NS_KW,
    )
    from autoapply.execute.submitter.fillers.react_select import (
        _DECLINE_KEYWORDS as RS_KW,
    )

    # Same content (both loaded from the same YAML key).
    assert NS_KW == RS_KW
    # Non-empty + contains the canonical phrasings.
    assert "decline" in NS_KW
    assert "prefer not" in NS_KW
    assert "not wish" in NS_KW


# ─── fillers/__init__ public surface contract ───────────────────────────


def test_fillers_exposes_expected_public_surface():
    from autoapply.execute.submitter import fillers

    # The four high-level fillers.
    for name in ("fill_field", "fill_combobox", "fill_select", "fill_radio"):
        assert callable(getattr(fillers, name)), f"{name} not callable"

    # Underscore-prefixed helpers that legacy tests reach for.
    for name in (
        "_is_react_select",
        "_combobox_has_value",
        "_read_input_label",
        "_normalize_tokens",
        "_looks_like_placeholder",
    ):
        assert callable(getattr(fillers, name)), f"{name} not callable"


# ─── detect.py — stub-driven probe tests ────────────────────────────────


class _StubEl:
    """Minimal Playwright-element stub for probing ``_is_react_select``.

    Returns canned values for ``get_attribute(role)`` and ``locator(...)``;
    everything else raises.
    """

    def __init__(self, role: str = "", has_ancestor: bool = False):
        self._role = role
        self._has_ancestor = has_ancestor

    def get_attribute(self, attr: str) -> str | None:
        if attr == "role":
            return self._role
        return None

    def locator(self, _sel: str) -> "_StubEl":
        return self

    def count(self) -> int:
        return 1 if self._has_ancestor else 0


def test_is_react_select_via_combobox_role():
    from autoapply.execute.submitter.fillers.detect import _is_react_select

    assert _is_react_select(_StubEl(role="combobox")) is True


def test_is_react_select_via_ancestor_control_class():
    from autoapply.execute.submitter.fillers.detect import _is_react_select

    assert _is_react_select(_StubEl(role="", has_ancestor=True)) is True


def test_is_react_select_plain_input_returns_false():
    from autoapply.execute.submitter.fillers.detect import _is_react_select

    assert _is_react_select(_StubEl(role="", has_ancestor=False)) is False
