"""Tests for the expand_repeating_groups phase."""

from __future__ import annotations

from autoapply.execute.submitter.phases.expand_groups import (
    MAX_ROWS,
    _classify_button,
    _count,
    _count_rows,
    expand_repeating_groups,
)


# ── _count ──────────────────────────────────────────────────────────


class _Profile:
    def __init__(self, experiences=None, education=None):
        self.experiences = experiences or []
        self.education = education or []


def test_count_handles_none_profile():
    assert _count(None, "experiences") == 0


def test_count_returns_zero_for_missing_attr():
    assert _count(_Profile(), "made_up_attr") == 0


def test_count_returns_length_of_list():
    assert _count(_Profile(experiences=[1, 2, 3]), "experiences") == 3


# ── Stub Page / Locator / Button ────────────────────────────────────


class _Locator:
    def __init__(self, items):
        self._items = items

    def all(self):
        return list(self._items)

    def count(self):
        return len(self._items)


class _Button:
    """Stub button that records click() calls + simulates row creation."""

    def __init__(self, kind: str, page: "_Page"):
        self.kind = kind
        self.page = page
        self.clicks = 0

    def evaluate(self, _js):
        # The classifier walks preceding-sibling input ids — emulate by
        # returning the kind-appropriate id pattern.
        if self.kind == "employment":
            return ["company-name-0", "title-0"]
        if self.kind == "education":
            return ["school--0", "degree--0"]
        return []

    def scroll_into_view_if_needed(self, **_kw):
        return None

    def click(self, **_kw):
        # Each click adds a new row — track via the page.
        self.clicks += 1
        if self.kind == "employment":
            self.page.employment_rows += 1
        elif self.kind == "education":
            self.page.education_rows += 1


class _Page:
    def __init__(self, employment_rows=1, education_rows=1):
        # Initial-state row counts; "Add another" clicks bump them.
        self.employment_rows = employment_rows
        self.education_rows = education_rows
        self._buttons = [
            _Button("employment", self),
            _Button("education", self),
        ]

    def locator(self, selector):
        if selector == "button.add-another-button":
            return _Locator(self._buttons)
        if selector == 'input[id^="company-name-"]':
            return _Locator([0] * self.employment_rows)
        if selector == 'input[id^="school--"]':
            return _Locator([0] * self.education_rows)
        return _Locator([])


def _emp_clicks(page):
    return next(b.clicks for b in page._buttons if b.kind == "employment")


def _edu_clicks(page):
    return next(b.clicks for b in page._buttons if b.kind == "education")


# ── _classify_button ────────────────────────────────────────────────


def test_classify_employment_button():
    page = _Page()
    btn = page._buttons[0]
    assert _classify_button(page, btn) == "employment"


def test_classify_education_button():
    page = _Page()
    btn = page._buttons[1]
    assert _classify_button(page, btn) == "education"


def test_classify_unknown_button():
    class _UnkButton:
        def evaluate(self, _js):
            return ["foo", "bar"]

    assert _classify_button(_Page(), _UnkButton()) == "unknown"


# ── expand_repeating_groups ─────────────────────────────────────────


def test_expand_clicks_add_another_for_each_extra_experience():
    """4 experiences → click employment 'Add another' 3 times."""
    page = _Page(employment_rows=1, education_rows=1)
    profile = _Profile(experiences=[1, 2, 3, 4], education=[1])
    audit = expand_repeating_groups(page, profile)
    assert _emp_clicks(page) == 3
    assert _edu_clicks(page) == 0
    assert audit["employment_rows"] == 4
    assert audit["education_rows"] == 1


def test_expand_clicks_education_too():
    page = _Page(employment_rows=1, education_rows=1)
    profile = _Profile(experiences=[1, 2], education=[1, 2, 3])
    expand_repeating_groups(page, profile)
    assert _emp_clicks(page) == 1
    assert _edu_clicks(page) == 2


def test_expand_caps_at_max_rows():
    """10 experiences but MAX_ROWS=5 → only 4 clicks (5 total rows)."""
    page = _Page(employment_rows=1, education_rows=1)
    profile = _Profile(experiences=list(range(10)), education=[])
    expand_repeating_groups(page, profile)
    assert _emp_clicks(page) == MAX_ROWS - 1


def test_expand_noop_when_one_or_zero_entries():
    """Profile has 0-1 entries → no clicks (the default row covers it)."""
    page = _Page()
    profile = _Profile(experiences=[1], education=[])
    expand_repeating_groups(page, profile)
    assert _emp_clicks(page) == 0
    assert _edu_clicks(page) == 0


def test_expand_handles_none_profile():
    """No profile → no clicks, no crash. Early return with zeros
    in the audit — the caller doesn't care about the difference
    between "no expansion needed" and "nothing was expanded."""
    page = _Page()
    audit = expand_repeating_groups(page, None)
    assert _emp_clicks(page) == 0
    assert _edu_clicks(page) == 0
    assert audit == {"employment_rows": 0, "education_rows": 0}


def test_expand_handles_idempotent_rerun():
    """If the form already has 4 rows, no further clicks are needed."""
    page = _Page(employment_rows=4, education_rows=2)
    profile = _Profile(experiences=[1, 2, 3, 4], education=[1, 2])
    expand_repeating_groups(page, profile)
    assert _emp_clicks(page) == 0
    assert _edu_clicks(page) == 0
