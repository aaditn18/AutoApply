"""Parser tests for the Jake Gutierrez template.

Uses the real resume .tex files when the submodule is checked out; otherwise
falls back to the absolute path in Aadit's local worktree. If neither is
available, the full-file tests skip — unit helpers still run.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from autoapply.profile.schema import DateRange, Experience
from autoapply.profile.tex_parser import (
    _balanced_arg,
    _read_n_args,
    _strip_latex,
    compute_yoe,
    parse_date_range,
    parse_tex,
    parse_tex_file,
)


# -- Resume fixture discovery ------------------------------------------------
# Prefer the submodule at `<repo>/resumes/`. Fallback to Aadit's local path for
# developer-machine runs pre-submodule.

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CANDIDATES = (
    _REPO_ROOT / "resumes",
    Path("/Users/aaditnilay/Downloads/Resume Stuff/aadit_nilay_resume_swe"),
    Path(
        "/Users/aaditnilay/Downloads/Resume Stuff/aadit_nilay_resume_swe/"
        ".claude/worktrees/crazy-diffie"
    ),
)


def _find_resume_dir() -> Path | None:
    for c in _CANDIDATES:
        if (c / "aadit_nilay_resume_swe.tex").exists():
            return c
    return None


RESUME_DIR = _find_resume_dir()
requires_resumes = pytest.mark.skipif(
    RESUME_DIR is None, reason="resume .tex files unavailable (submodule not checked out)"
)

ACTIVE_TRACKS = ("swe", "ml", "hpc", "quant")


# -- Low-level helper tests --------------------------------------------------


def test_balanced_arg_simple():
    inner, end = _balanced_arg("{hello}world", 0)
    assert inner == "hello"
    assert end == 7


def test_balanced_arg_nested():
    s = "{outer{inner}text}rest"
    inner, end = _balanced_arg(s, 0)
    assert inner == "outer{inner}text"
    assert s[end:] == "rest"


def test_read_n_args_skips_whitespace():
    args, _ = _read_n_args("{a} {b}  {c}", 0, 3)
    assert args == ["a", "b", "c"]


def test_strip_latex_common_macros():
    assert _strip_latex(r"\textbf{Hello} World") == "Hello World"
    assert _strip_latex(r"\emph{italic} text") == "italic text"
    assert _strip_latex(r"\underline{foo}") == "foo"
    assert _strip_latex(r"Saved \$1.5B in fees") == "Saved $1.5B in fees"
    assert _strip_latex(r"$\sim 200\text{ ms}$") == "~ 200 ms"


def test_strip_latex_href():
    assert _strip_latex(r"\href{https://x.com}{Visit X}") == "Visit X"


# -- Date parsing -----------------------------------------------------------


def test_parse_date_range_standard():
    dr = parse_date_range("May 2025 -- Aug 2025")
    assert dr.start == date(2025, 5, 1)
    assert dr.end is not None
    assert dr.end.year == 2025 and dr.end.month == 8
    assert not dr.is_present


def test_parse_date_range_present():
    dr = parse_date_range("Feb 2026 -- Present")
    assert dr.start == date(2026, 2, 1)
    assert dr.is_present
    assert dr.end is None


def test_parse_date_range_single_year():
    dr = parse_date_range("CVPRW 2026")
    assert dr.start == date(2026, 1, 1)


# -- YOE math ---------------------------------------------------------------


def test_compute_yoe_union_overlap():
    e1 = Experience(
        title="A", company="X",
        stack=["Python"],
        date_range=DateRange(raw="", start=date(2024, 1, 1), end=date(2024, 12, 31)),
    )
    e2 = Experience(
        title="B", company="Y",
        stack=["Python"],
        date_range=DateRange(raw="", start=date(2024, 6, 1), end=date(2025, 5, 31)),
    )
    yoe = compute_yoe([e1, e2], today=date(2026, 1, 1))
    # Union: Jan 2024 - May 2025 -> 16 months -> ~1.3 years
    assert yoe["Python"] == pytest.approx(1.3, abs=0.05)


def test_compute_yoe_ongoing_uses_today():
    e = Experience(
        title="Now", company="Z",
        stack=["Rust"],
        date_range=DateRange(raw="", start=date(2025, 1, 1), end=None, is_present=True),
    )
    yoe = compute_yoe([e], today=date(2026, 1, 1))
    assert yoe["Rust"] == pytest.approx(1.0, abs=0.05)


# -- End-to-end against the real resumes ------------------------------------


@requires_resumes
@pytest.mark.parametrize("track", ACTIVE_TRACKS)
def test_parse_real_resume_nonempty(track):
    path = RESUME_DIR / f"aadit_nilay_resume_{track}.tex"
    profile = parse_tex_file(path, track)
    assert profile.track == track
    assert profile.full_name == "Aadit Nilay"
    assert profile.email
    assert profile.linkedin_url.startswith("https://")
    assert profile.github_url.startswith("https://")
    assert len(profile.education) == 1
    assert profile.education[0].school.lower().startswith("university of maryland")
    # Every active track resume should have at least three experiences
    assert len(profile.experiences) >= 3
    # Every experience must have a parseable start date
    for exp in profile.experiences:
        assert exp.date_range.start is not None, f"{track}: bad date {exp.date_range.raw!r}"


@requires_resumes
def test_swe_track_has_projects_and_skills():
    profile = parse_tex_file(RESUME_DIR / "aadit_nilay_resume_swe.tex", "swe")
    assert len(profile.projects) >= 2
    # SWE resume lists Languages, Libraries, Tools
    assert profile.skills.languages, "no languages parsed"
    assert profile.skills.libraries, "no libraries parsed"
    assert profile.skills.tools, "no tools parsed"
    assert "Python" in profile.skills.languages
    assert "Java" in profile.skills.languages


@requires_resumes
def test_quant_track_has_trading_experience():
    profile = parse_tex_file(RESUME_DIR / "aadit_nilay_resume_quant.tex", "quant")
    titles = [e.title.lower() for e in profile.experiences]
    assert any("quant" in t or "trading" in t for t in titles), (
        "quant resume should surface a quant/trading title"
    )


@requires_resumes
@pytest.mark.parametrize("track", ACTIVE_TRACKS)
def test_yoe_computed(track):
    """YOE table must be non-empty and every value must be positive."""
    profile = parse_tex_file(RESUME_DIR / f"aadit_nilay_resume_{track}.tex", track)
    yoe = profile.years_of_experience
    assert yoe, f"{track}: years_of_experience is empty"
    assert all(v > 0 for v in yoe.values()), (
        f"{track}: non-positive YOE entries: {[k for k, v in yoe.items() if v <= 0]}"
    )
    # A skill that appears in multiple overlapping experiences should exceed
    # the shortest single-experience contribution (~0.2–0.3 y).
    assert max(yoe.values()) >= 0.5, (
        f"{track}: max YOE too small: {max(yoe.values())}"
    )


@requires_resumes
def test_yoe_swe_has_aws_multi_experience():
    """On the SWE resume, AWS appears in 3 experiences — YOE should reflect that."""
    profile = parse_tex_file(RESUME_DIR / "aadit_nilay_resume_swe.tex", "swe")
    yoe = profile.years_of_experience
    # Known multi-experience skills on the SWE resume
    assert "AWS" in yoe
    assert yoe["AWS"] >= 0.7, f"AWS YOE unexpectedly low: {yoe['AWS']}"


@requires_resumes
def test_parse_tex_is_idempotent():
    """parse_tex() twice on the same input must produce identical output."""
    path = RESUME_DIR / "aadit_nilay_resume_swe.tex"
    text = path.read_text(encoding="utf-8")
    p1 = parse_tex(text, "swe")
    p2 = parse_tex(text, "swe")
    assert p1.model_dump() == p2.model_dump()
