"""Pydantic schemas for API responses.

We deliberately do NOT pass ORM models out — the schemas here are
stable wire contracts that the Next.js frontend (via auto-generated
TypeScript types) depends on. Adding a field is non-breaking; renaming
or removing one is.

Schema-naming convention: ``<Resource>Out`` for read responses,
``<Resource>In`` for write bodies.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    """Common config: allow ORM mode and forbid extras."""
    model_config = ConfigDict(from_attributes=True, extra="ignore")


# ── Jobs ────────────────────────────────────────────────────────────


class JobScoring(_Base):
    """Scoring breakdown — all four signals + the final sum."""
    base_fit: float | None = None
    pay_signal: float | None = None
    pay_midpoint: float | None = None
    loc_signal: float | None = None
    freshness_signal: float | None = None
    final_rank: float | None = None


class JobOut(_Base):
    """Read-list shape — the Jobs page table row."""
    id: int
    canonical_key: str
    source: str
    board_token: str
    url: str
    title: str
    company: str
    location: str
    department: str
    employment_type: str
    posted_at: str
    track: str | None
    status: str
    us_eligible: bool
    injection_detected: bool
    scoring: JobScoring
    application_count: int = 0
    created_at: datetime
    updated_at: datetime


class JobDetailOut(JobOut):
    """Job detail page — adds the full description + related apps."""
    description: str
    sightings: dict[str, Any]
    meta: dict[str, Any]
    applications: list["ApplicationOut"] = Field(default_factory=list)


# ── Applications ────────────────────────────────────────────────────


class ResolvedField(_Base):
    """One row in the Application Detail field-audit panel."""
    label: str
    kind: str
    value: Any
    source: str           # profile / bank / classifier / llm_* / needs_review
    confidence: float | None = None


class ApplicationOut(_Base):
    """Read-list shape — the Applications page table row."""
    id: int
    job_id: int
    job_title: str
    job_company: str
    job_source: str
    track_submitted: str
    dry_run: bool
    outcome: str
    error_code: str
    error_message: str
    resolved_field_count: int
    llm_model: str | None = None
    submitted_at: datetime


class ApplicationDetailOut(ApplicationOut):
    """Application detail — three panels worth of data."""
    answers: dict[str, Any]
    cover_letter_text: str
    artifacts: dict[str, Any]
    resolved_fields: list[ResolvedField]
    review_flags: list["ReviewFlagOut"] = Field(default_factory=list)
    screenshot_url: str | None = None
    job_url: str
    job_description: str = ""


# ── Review queue ────────────────────────────────────────────────────


class ReviewFlagOut(_Base):
    """One flagged form field."""
    id: int
    job_id: int
    application_id: int | None
    field_name: str
    field_label: str
    field_kind: str
    required: bool
    options: list[str]
    reason: str
    question_type: str | None
    attempted_value: str
    created_at: datetime


class SpamRejectOut(_Base):
    """One spam-flag failure (Ashby risk-engine rejection)."""
    application_id: int
    job_id: int
    job_title: str
    job_company: str
    job_url: str
    submitted_at: datetime
    error_code: str
    error_message: str


# ── Dashboard ───────────────────────────────────────────────────────


class OutcomeCounts(_Base):
    ok: int = 0
    failed: int = 0
    review: int = 0
    captcha: int = 0
    dry_run: int = 0
    pending: int = 0


class SourceSpamRate(_Base):
    source: str
    total_failed: int
    spam_flagged: int
    spam_rate: float


class TopJobOut(_Base):
    """Compact entry for the dashboard's top-unapplied list."""
    id: int
    title: str
    company: str
    track: str | None
    final_rank: float | None
    url: str


class DashboardOut(_Base):
    total_jobs: int
    scored_jobs: int
    unapplied_scored: int
    apps_total: int
    apps_last_7d: int
    outcomes: OutcomeCounts
    spam_rates: list[SourceSpamRate]
    top_unapplied: list[TopJobOut]
    velocity: list["VelocityPoint"] = []  # forward-ref
    last_updated: datetime


# ── Common envelopes ────────────────────────────────────────────────


class Page(_Base):
    """Generic pagination wrapper."""
    items: list[Any]
    total: int
    page: int
    page_size: int


# ── Phase 2: settings ────────────────────────────────────────────────


class TrackProfileSummary(_Base):
    """Per-track summary for the read-only Profile tab."""
    track: str
    full_name: str
    email: str
    phone: str
    linkedin_url: str
    github_url: str
    skills_count: int
    experiences_count: int
    projects_count: int
    education_count: int


class ProfileSnapshot(_Base):
    """Top-level Profile tab payload."""
    tracks: list[TrackProfileSummary]
    raw_path: str


class AnswerBankPayload(_Base):
    """GET /api/answer_bank — full YAML as text + parsed."""
    yaml_text: str
    parsed: dict[str, Any]
    keys: list[str]


class AnswerBankWriteIn(_Base):
    """PUT /api/answer_bank — body."""
    yaml_text: str


class AnswerBankPreviewIn(_Base):
    """POST /api/answer_bank/preview — preview a single key resolution."""
    question_type: str
    track: str | None = None


class AnswerBankPreviewOut(_Base):
    question_type: str
    track: str | None
    value: str | None
    found: bool


class CompaniesPayload(_Base):
    """GET /api/companies — grid view."""
    sources: dict[str, dict[str, list[str]]]
    """{ 'greenhouse': { 'test_safe': [...], 'live_only': [...] }, ... }"""


class CompaniesWriteIn(_Base):
    """PUT /api/companies — replace the grid."""
    sources: dict[str, dict[str, list[str]]]


class EnvKey(_Base):
    """One row in the .env editor."""
    key: str
    value: str           # masked for secret keys
    is_secret: bool
    is_set: bool         # True if the file actually has it


class EnvPayload(_Base):
    """GET /api/env — current values + schema hints."""
    keys: list[EnvKey]
    raw_path: str


class EnvWriteIn(_Base):
    """PUT /api/env — partial update; only provided keys are touched."""
    updates: dict[str, str]


class WriteResult(_Base):
    """Generic ack for PUT/POST endpoints that don't return a resource."""
    ok: bool
    message: str = ""
    backup_path: str | None = None


# ── Phase 3: pipeline runs ───────────────────────────────────────────


class PipelineTriggerIn(_Base):
    """Body for POST /api/pipeline/{stage}.

    All fields optional — each stage maps a subset to CLI flags.
    """
    sources: list[str] | None = None      # ingest only
    boards: list[str] | None = None       # ingest only
    limit: int | None = None              # all stages
    min_rank: float | None = None         # apply only
    dry_run: bool | None = None           # apply only — defaults to True


class RunMeta(_Base):
    """A row in /api/pipeline/runs."""
    run_id: str
    stage: str            # "ingest" | "score" | "apply" | "profile-build" | "manual"
    status: str           # "running" | "ok" | "failed"
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    summary: str          # parsed CLI summary line, e.g. "scored=12 ok=3"
    cmd: str              # the actual shell command that ran
    log_size: int


class RetryOut(_Base):
    """Result of POST /api/applications/{id}/retry."""
    ok: bool
    new_application_id: int | None
    outcome: str
    message: str


class BatchApplyIn(_Base):
    """Body for POST /api/applications/batch."""
    job_ids: list[int]
    dry_run: bool = True


class BatchApplyOut(_Base):
    started: int
    run_id: str


class FlagResolveIn(_Base):
    """Body for POST /api/review/flags/{flag_id}/resolve."""
    value: str
    bank_key: str | None = None     # override the auto-derived key


class FlagResolveOut(_Base):
    ok: bool
    bank_key_written: str
    retry_run_id: str | None = None


# ── Phase 4: resumes + LLM audit ─────────────────────────────────────


class ResumeOut(_Base):
    track: str
    pdf_path: str
    pdf_size: int
    tex_present: bool
    txt_present: bool
    last_modified: datetime | None


class LLMAuditRow(_Base):
    application_id: int
    submitted_at: datetime
    job_company: str
    job_title: str
    model_used: str
    batch_asked_count: int
    cascade_trace: list[Any]   # entries are either str or {model, status, ...}
    error: str
    answer_count: int


class VelocityPoint(_Base):
    date: str        # YYYY-MM-DD
    apps: int
    ok: int
    failed: int


# Resolve forward refs
JobDetailOut.model_rebuild()
ApplicationDetailOut.model_rebuild()
DashboardOut.model_rebuild()
