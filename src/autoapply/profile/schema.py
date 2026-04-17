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
