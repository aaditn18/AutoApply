"""Pydantic models for the parsed resume profile.

One `Profile` per resume_track (swe/ml/hpc/quant). All fields are extracted
deterministically from `.tex` files — see `profile.tex_parser`.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


Track = Literal["swe", "ml", "hpc", "quant", "all", "new"]


class DateRange(BaseModel):
    """A month-precision date range like 'May 2025 -- Aug 2025' or 'Feb 2026 -- Present'."""

    raw: str
    start: date | None = None
    end: date | None = None          # None == ongoing / "Present"
    is_present: bool = False

    def months(self, today: date | None = None) -> int:
        """Duration in whole months. Zero if start is unknown."""
        if self.start is None:
            return 0
        end = self.end if self.end is not None else (today or date.today())
        return max(0, (end.year - self.start.year) * 12 + (end.month - self.start.month))


class Experience(BaseModel):
    title: str
    company: str
    location: str = ""
    stack: list[str] = Field(default_factory=list)
    date_range: DateRange
    bullets: list[str] = Field(default_factory=list)


class Project(BaseModel):
    name: str
    stack: list[str] = Field(default_factory=list)
    date_range: DateRange
    bullets: list[str] = Field(default_factory=list)


class Education(BaseModel):
    school: str
    location: str = ""
    degree: str
    date_range: DateRange
    minor: str = ""
    gpa: str = ""
    coursework: list[str] = Field(default_factory=list)


class Skills(BaseModel):
    """Flat skill buckets plus a generic `by_category` catch-all."""

    languages: list[str] = Field(default_factory=list)
    libraries: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    by_category: dict[str, list[str]] = Field(default_factory=dict)

    def all(self) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for bucket in (self.languages, self.libraries, self.tools):
            for s in bucket:
                if s.lower() not in seen:
                    seen.add(s.lower())
                    out.append(s)
        for items in self.by_category.values():
            for s in items:
                if s.lower() not in seen:
                    seen.add(s.lower())
                    out.append(s)
        return out


class Profile(BaseModel):
    """The full parsed profile for a single resume track."""

    track: Track
    full_name: str
    email: str = ""
    phone: str = ""
    linkedin_url: str = ""
    github_url: str = ""

    education: list[Education] = Field(default_factory=list)
    experiences: list[Experience] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    skills: Skills = Field(default_factory=Skills)

    # Computed at build time: skill -> years of exposure (union of overlapping
    # experience intervals). Used for deterministic YOE answers.
    years_of_experience: dict[str, float] = Field(default_factory=dict)

    # ── Common answers (same across every resume track) ────────────────
    # These are populated from ``state/profile.json``'s per-track blocks
    # with identical values — having them on Profile lets the classifier
    # + bank route EEO / immigration / willing-to-work questions
    # directly from deterministic data rather than relying on the LLM.
    # When the candidate's situation changes (e.g., OPT → H-1B, more
    # willing states), update these in one place.

    # EEO / demographic
    demo_gender: str = "Decline to self-identify"
    demo_race: str = "Decline to self-identify"
    demo_hispanic_latino: str = "No"
    demo_veteran: str = "I am not a protected veteran"
    demo_disability: str = "I do not wish to answer"
    demo_pronouns: str = "He/Him"
    demo_sexual_orientation: str = "Decline to self-identify"
    demo_transgender: str = "Decline to self-identify"

    # Military / prior-service
    military_service: str = "No"

    # Citizenship / immigration
    citizenship_country: str = "India"
    us_citizen: str = "No"
    work_authorized_us: str = "Yes"
    permanent_work_authorization: str = "No"   # green-card-equivalent
    require_sponsorship_now: str = "No"         # OPT covers near-term
    require_sponsorship_future: str = "Yes"     # H-1B after OPT
    visa_status: str = "F-1 visa OPT (STEM extension till 2029)"

    # Current location logistics
    current_city: str = "College Park"
    current_state: str = "MD"
    current_state_full: str = "Maryland"
    current_zip: str = "20740"
    current_country: str = "United States"
    current_location: str = "College Park, MD"

    # Willing-to-work locations — defaults to all 50 states + DC. The
    # multi-state checkbox grid on some ATS (mthree) uses this list to
    # check every one. When the candidate limits relocation to a few
    # target states, reduce this list.
    willing_to_work_states: list[str] = Field(default_factory=lambda: [
        "Alabama", "Alaska", "Arizona", "Arkansas", "California",
        "Colorado", "Connecticut", "Delaware", "Florida", "Georgia",
        "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas",
        "Kentucky", "Louisiana", "Maine", "Maryland", "Massachusetts",
        "Michigan", "Minnesota", "Mississippi", "Missouri", "Montana",
        "Nebraska", "Nevada", "New Hampshire", "New Jersey",
        "New Mexico", "New York", "North Carolina", "North Dakota",
        "Ohio", "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island",
        "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah",
        "Vermont", "Virginia", "Washington", "Washington DC",
        "West Virginia", "Wisconsin", "Wyoming",
    ])
