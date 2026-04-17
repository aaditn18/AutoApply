"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-04-16

Creates all tables. The migration mirrors Base.metadata as of the
initial commit; future migrations will use autogenerate with batch ops
(SQLite can't drop columns without rebuild).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("canonical_key", sa.String(32), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("board_token", sa.String(128), nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("company", sa.String(256), nullable=False),
        sa.Column("location", sa.String(256), server_default=""),
        sa.Column("department", sa.String(256), server_default=""),
        sa.Column("description", sa.Text, server_default=""),
        sa.Column("posted_at", sa.String(64), server_default=""),
        sa.Column("updated_at_source", sa.String(64), server_default=""),
        sa.Column("employment_type", sa.String(64), server_default=""),
        sa.Column("track", sa.String(8), nullable=True),
        sa.Column("base_fit", sa.Float, nullable=True),
        sa.Column("pay_midpoint", sa.Float, nullable=True),
        sa.Column("pay_signal", sa.Float, nullable=True),
        sa.Column("loc_signal", sa.Float, nullable=True),
        sa.Column("final_rank", sa.Float, nullable=True),
        sa.Column("us_eligible", sa.Boolean, server_default=sa.text("1")),
        sa.Column("injection_detected", sa.Boolean, server_default=sa.text("0")),
        sa.Column("status", sa.String(32), nullable=False, server_default="new"),
        sa.Column("sightings", sa.JSON, nullable=False),
        sa.Column("meta", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("canonical_key", name="uq_jobs_canonical_key"),
    )
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_final_rank", "jobs", ["final_rank"])
    op.create_index("ix_jobs_company", "jobs", ["company"])

    op.create_table(
        "applications",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.Integer, sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("track_submitted", sa.String(8), nullable=False),
        sa.Column("resume_sha", sa.String(64), server_default=""),
        sa.Column("dry_run", sa.Boolean, nullable=False, server_default=sa.text("1")),
        sa.Column("outcome", sa.String(32), server_default="pending"),
        sa.Column("error_code", sa.String(64), server_default=""),
        sa.Column("error_message", sa.Text, server_default=""),
        sa.Column("answers", sa.JSON, nullable=False),
        sa.Column("cover_letter_text", sa.Text, server_default=""),
        sa.Column("artifacts", sa.JSON, nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_applications_job_id", "applications", ["job_id"])
    op.create_index("ix_applications_submitted_at", "applications", ["submitted_at"])
    op.create_index("ix_applications_dry_run", "applications", ["dry_run"])

    op.create_table(
        "answer_bank_entries",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("question_type", sa.String(64), nullable=False),
        sa.Column("track_key", sa.String(16), nullable=False, server_default="_default"),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("confirmed_by", sa.String(64), server_default=""),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("question_type", "track_key", name="uq_ab_type_track"),
    )

    op.create_table(
        "events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.Integer, sa.ForeignKey("jobs.id"), nullable=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("detail", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_events_job_id", "events", ["job_id"])
    op.create_index("ix_events_kind", "events", ["kind"])

    op.create_table(
        "security_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.Integer, sa.ForeignKey("jobs.id"), nullable=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("pattern_matched", sa.String(128), server_default=""),
        sa.Column("snippet", sa.Text, server_default=""),
        sa.Column("source_url", sa.Text, server_default=""),
        sa.Column("severity", sa.String(16), server_default="medium"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_security_events_created_at", "security_events", ["created_at"])
    op.create_index("ix_security_events_kind", "security_events", ["kind"])


def downgrade() -> None:
    op.drop_index("ix_security_events_kind", "security_events")
    op.drop_index("ix_security_events_created_at", "security_events")
    op.drop_table("security_events")
    op.drop_index("ix_events_kind", "events")
    op.drop_index("ix_events_job_id", "events")
    op.drop_table("events")
    op.drop_table("answer_bank_entries")
    op.drop_index("ix_applications_dry_run", "applications")
    op.drop_index("ix_applications_submitted_at", "applications")
    op.drop_index("ix_applications_job_id", "applications")
    op.drop_table("applications")
    op.drop_index("ix_jobs_company", "jobs")
    op.drop_index("ix_jobs_final_rank", "jobs")
    op.drop_index("ix_jobs_status", "jobs")
    op.drop_table("jobs")
