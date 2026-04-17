"""SQLAlchemy ORM models.

All persistent state lives in SQLite (`state/jobs.sqlite`), which is
committed to the repo via the pipeline workflow. Schema choices are
biased toward append-only / auditable — we want to be able to re-score
offline and audit every decision.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# -- Jobs -------------------------------------------------------------------


class Job(Base):
    """A unique posting, keyed by canonical_key. Deduped across sources."""

    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("canonical_key", name="uq_jobs_canonical_key"),
        Index("ix_jobs_status", "status"),
        Index("ix_jobs_final_rank", "final_rank"),
        Index("ix_jobs_company", "company"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_key: Mapped[str] = mapped_column(String(32), nullable=False)

    # Source fingerprint (first source that saw the job — later sightings
    # append to `sightings` JSON instead of creating a new row)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    board_token: Mapped[str] = mapped_column(String(128), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)

    # Posting facts
    title: Mapped[str] = mapped_column(Text, nullable=False)
    company: Mapped[str] = mapped_column(String(256), nullable=False)
    location: Mapped[str] = mapped_column(String(256), default="")
    department: Mapped[str] = mapped_column(String(256), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    posted_at: Mapped[str] = mapped_column(String(64), default="")
    updated_at_source: Mapped[str] = mapped_column(String(64), default="")
    employment_type: Mapped[str] = mapped_column(String(64), default="")

    # Selection / scoring (set during the select stage)
    track: Mapped[str | None] = mapped_column(String(8), nullable=True)
    base_fit: Mapped[float | None] = mapped_column(Float, nullable=True)
    pay_midpoint: Mapped[float | None] = mapped_column(Float, nullable=True)
    pay_signal: Mapped[float | None] = mapped_column(Float, nullable=True)
    loc_signal: Mapped[float | None] = mapped_column(Float, nullable=True)
    freshness_signal: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_rank: Mapped[float | None] = mapped_column(Float, nullable=True)
    us_eligible: Mapped[bool] = mapped_column(Boolean, default=True)
    injection_detected: Mapped[bool] = mapped_column(Boolean, default=False)

    # State machine. See `STATUSES` below for allowed values.
    status: Mapped[str] = mapped_column(String(32), default="new", nullable=False)

    # Additional sightings / raw payloads / track-picker rationale
    sightings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    applications: Mapped[list["Application"]] = relationship(
        "Application", back_populates="job", cascade="all, delete-orphan"
    )
    events: Mapped[list["Event"]] = relationship(
        "Event", back_populates="job", cascade="all, delete-orphan"
    )


STATUSES = frozenset({
    "new",                       # just ingested
    "scored",                    # passed selection, has final_rank
    "rejected_by_location",
    "rejected_by_injection",
    "rejected_by_company_cap",
    "rejected_by_rank",
    "rejected_by_yoe",             # job requires > 2 years experience
    "queued_auto",               # will be auto-applied next pipeline run
    "queued_review",             # open GitHub Issue awaiting /approve
    "applied_ok",
    "applied_failed",
    "applied_captcha",
    "archived",
})


# -- Applications (1 per real submit or dry_run) ---------------------------


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        Index("ix_applications_job_id", "job_id"),
        Index("ix_applications_submitted_at", "submitted_at"),
        Index("ix_applications_dry_run", "dry_run"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False)

    track_submitted: Mapped[str] = mapped_column(String(8), nullable=False)
    resume_sha: Mapped[str] = mapped_column(String(64), default="")  # submodule SHA
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), default="pending")  # ok / failed / captcha
    error_code: Mapped[str] = mapped_column(String(64), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")

    # Every answer we filled, keyed by question_type — deterministic audit.
    answers: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    cover_letter_text: Mapped[str] = mapped_column(Text, default="")

    # Artifact paths (PNG screenshot, JSON payload dump) for DRY_RUN auditing.
    artifacts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    job: Mapped[Job] = relationship("Job", back_populates="applications")


# -- Answer bank entries (runtime learnings — supplements YAML) ------------


class AnswerBankEntry(Base):
    """Optional runtime-learned answers. YAML is authoritative for seeded
    values; this table stores additions the user confirmed via /approve on
    a review issue. Keyed by (question_type, track_key)."""

    __tablename__ = "answer_bank_entries"
    __table_args__ = (
        UniqueConstraint("question_type", "track_key", name="uq_ab_type_track"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    question_type: Mapped[str] = mapped_column(String(64), nullable=False)
    track_key: Mapped[str] = mapped_column(String(16), default="_default", nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_by: Mapped[str] = mapped_column(String(64), default="")
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# -- Events (audit trail per job) ------------------------------------------


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_job_id", "job_id"),
        Index("ix_events_kind", "kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    job: Mapped[Job | None] = relationship("Job", back_populates="events")


# -- Security events (append-only; immutable) -----------------------------


class SecurityEvent(Base):
    """Prompt-injection hits + validate_output failures. Never delete —
    weekly `security-report` reads this."""

    __tablename__ = "security_events"
    __table_args__ = (
        Index("ix_security_events_created_at", "created_at"),
        Index("ix_security_events_kind", "kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    pattern_matched: Mapped[str] = mapped_column(String(128), default="")
    snippet: Mapped[str] = mapped_column(Text, default="")
    source_url: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[str] = mapped_column(String(16), default="medium")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
