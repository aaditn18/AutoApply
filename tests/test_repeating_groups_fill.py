"""Tests for the deterministic Employment / Education filler."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from autoapply.execute.submitter.phases.repeating_groups_fill import (
    _split_degree,
    fill_repeating_groups,
)


# ── _split_degree ───────────────────────────────────────────────────


def test_split_degree_bs_with_majors():
    assert _split_degree("B.S. Computer Science, Mathematics") == (
        "B.S.", "Computer Science, Mathematics",
    )


def test_split_degree_ms():
    assert _split_degree("M.S. Statistics") == ("M.S.", "Statistics")


def test_split_degree_phd():
    assert _split_degree("Ph.D. Physics") == ("Ph.D.", "Physics")


def test_split_degree_bachelor_of_science():
    out = _split_degree("Bachelor of Science Computer Science")
    assert out[0].lower().startswith("bachelor")
    assert "Computer Science" in out[1]


def test_split_degree_falls_back_when_no_prefix():
    assert _split_degree("Just a discipline name") == (
        "Just a discipline name", "",
    )


# ── Stub Page + locator chain ───────────────────────────────────────


class _Element:
    def __init__(self):
        self.fill_calls: list[str] = []
        self.checked = False

    def fill(self, value, **_kw):
        self.fill_calls.append(value)

    def check(self, **_kw):
        self.checked = True


class _Locator:
    def __init__(self, element: _Element | None):
        self._el = element
        self.first = element

    def count(self):
        return 1 if self._el is not None else 0


class _Page:
    """Stub Page that registers element ids → _Element instances."""

    def __init__(self):
        self.elements: dict[str, _Element] = {}
        self.combobox_calls: list[tuple[str, str]] = []

    def register(self, element_id: str) -> _Element:
        el = _Element()
        self.elements[element_id] = el
        return el

    def locator(self, selector):
        # Selector is either '#id' or '[id="id"]'.
        if selector.startswith("#"):
            eid = selector[1:].replace("\\#", "#").replace('\\"', '"')
        elif selector.startswith('[id="'):
            eid = selector[5:-2]
        else:
            return _Locator(None)
        return _Locator(self.elements.get(eid))


# ── fill_repeating_groups (employment) ───────────────────────────────


class _DateRange:
    def __init__(self, start, end, is_present=False):
        self.start = start
        self.end = end
        self.is_present = is_present


class _Experience:
    def __init__(self, company, title, dr):
        self.company = company
        self.title = title
        self.date_range = dr


class _Education:
    def __init__(self, school, degree):
        self.school = school
        self.degree = degree


class _Profile:
    def __init__(self, experiences=None, education=None):
        self.experiences = experiences or []
        self.education = education or []


def test_employment_text_inputs_filled(monkeypatch):
    """Company-name-N + title-N go through plain .fill() — stub
    fill_combobox so we don't need it for text-only verification."""
    page = _Page()
    page.register("company-name-0")
    page.register("title-0")
    page.register("start-date-month-0")
    page.register("start-date-year-0")
    page.register("end-date-month-0")
    page.register("end-date-year-0")

    captured: list[tuple[str, str]] = []

    def stub_combobox(_p, el, value, **_kw):
        # Identify the element by going back through page.elements
        for eid, e in page.elements.items():
            if e is el:
                captured.append((eid, value))
                return
        captured.append(("?", value))

    monkeypatch.setattr(
        "autoapply.execute.submitter.field_fill.fill_combobox",
        stub_combobox,
    )
    # Education prefer_patterns helper is also imported lazily — stub.
    monkeypatch.setattr(
        "autoapply.execute.submitter.dom.preferences._education_patterns_for_label",
        lambda _label: None,
    )

    profile = _Profile(
        experiences=[
            _Experience(
                "PayPal",
                "Backend SWE Intern",
                _DateRange(date(2025, 5, 1), date(2025, 8, 1)),
            ),
        ],
    )

    audit = fill_repeating_groups(page, profile)

    assert audit["employment_filled"] == 1
    # Text fields filled directly via .fill()
    assert page.elements["company-name-0"].fill_calls == ["PayPal"]
    assert page.elements["title-0"].fill_calls == ["Backend SWE Intern"]
    # Date fields routed through fill_combobox (the stub captured them)
    captured_by_id = dict(captured)
    assert captured_by_id["start-date-month-0"] == "May"
    assert captured_by_id["start-date-year-0"] == "2025"
    assert captured_by_id["end-date-month-0"] == "August"
    assert captured_by_id["end-date-year-0"] == "2025"


def test_employment_present_skips_end_date_and_checks_current(monkeypatch):
    page = _Page()
    page.register("company-name-0")
    page.register("title-0")
    page.register("start-date-month-0")
    page.register("start-date-year-0")
    page.register("end-date-month-0")
    page.register("end-date-year-0")
    page.register("current-role-0_1")

    monkeypatch.setattr(
        "autoapply.execute.submitter.field_fill.fill_combobox",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "autoapply.execute.submitter.dom.preferences._education_patterns_for_label",
        lambda _label: None,
    )

    profile = _Profile(
        experiences=[
            _Experience(
                "PayPal",
                "Senior Engineer",
                _DateRange(date(2025, 5, 1), None, is_present=True),
            ),
        ],
    )

    fill_repeating_groups(page, profile)

    # End date fields NOT filled (no calls captured by us anyway, but
    # the checkbox got checked).
    assert page.elements["current-role-0_1"].checked is True
    # End date elements were never written to (no fill calls).
    assert page.elements["end-date-month-0"].fill_calls == []


def test_education_routes_school_degree_discipline(monkeypatch):
    page = _Page()
    page.register("school--0")
    page.register("degree--0")
    page.register("discipline--0")

    captured: list[tuple[str, str]] = []
    def stub_combobox(_p, el, value, **_kw):
        for eid, e in page.elements.items():
            if e is el:
                captured.append((eid, value))
                return
    monkeypatch.setattr(
        "autoapply.execute.submitter.field_fill.fill_combobox",
        stub_combobox,
    )
    monkeypatch.setattr(
        "autoapply.execute.submitter.dom.preferences._education_patterns_for_label",
        lambda _label: None,
    )

    profile = _Profile(
        education=[
            _Education(
                "University of Maryland", "B.S. Computer Science, Mathematics",
            ),
        ],
    )

    audit = fill_repeating_groups(page, profile)

    assert audit["education_filled"] == 1
    captured_by_id = dict(captured)
    assert captured_by_id["school--0"] == "University of Maryland"
    assert captured_by_id["degree--0"] == "B.S."
    assert captured_by_id["discipline--0"] == "Computer Science, Mathematics"


def test_handles_none_profile():
    audit = fill_repeating_groups(_Page(), None)
    assert audit == {"employment_filled": 0, "education_filled": 0}


def test_caps_at_max_rows(monkeypatch):
    """7 experiences but MAX_ROWS=5 → only the first 5 attempted."""
    page = _Page()
    for i in range(5):
        page.register(f"company-name-{i}")
        page.register(f"title-{i}")
        page.register(f"start-date-month-{i}")
        page.register(f"start-date-year-{i}")
        page.register(f"end-date-month-{i}")
        page.register(f"end-date-year-{i}")
    monkeypatch.setattr(
        "autoapply.execute.submitter.field_fill.fill_combobox",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "autoapply.execute.submitter.dom.preferences._education_patterns_for_label",
        lambda _label: None,
    )

    profile = _Profile(
        experiences=[
            _Experience(
                f"Company {i}",
                f"Role {i}",
                _DateRange(date(2024, 1, 1), date(2024, 6, 1)),
            )
            for i in range(7)
        ]
    )
    audit = fill_repeating_groups(page, profile)
    assert audit["employment_filled"] == 5
