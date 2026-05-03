"""Hermetic test fixtures for the api/ FastAPI app.

Each test gets a fresh in-memory SQLite engine seeded with a small
deterministic graph (3 jobs × 2 apps × 2 review_flags). The
production engine is overridden via ``app.dependency_overrides``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool

from autoapply.tracker.db import session_scope
from autoapply.tracker.models import Application, Base, Job, ReviewFlag


def _create_shared_memory_engine():
    """In-memory SQLite shared across all connections (StaticPool).

    The default in-memory DB isolates tables per-connection. When
    FastAPI dispatches a request its dep-injection creates a fresh
    session, which gets a fresh empty connection. StaticPool reuses
    a single connection so the seeded tables stay visible.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
        echo=False,
    )

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _record):  # type: ignore[no-untyped-def]
        cur = dbapi_conn.cursor()
        try:
            cur.execute("PRAGMA foreign_keys=ON")
        finally:
            cur.close()

    Base.metadata.create_all(engine)
    return engine

from api.deps import get_engine
from api.main import app


@pytest.fixture
def seeded_engine():
    """In-memory engine with a tiny deterministic dataset.

    Layout:
      - job 1 (greenhouse, scored, applied OK)         → app 1
      - job 2 (ashby, scored, applied failed/spam)     → app 2 (review flag)
      - job 3 (lever, scored, no application)
    """
    engine = _create_shared_memory_engine()
    with session_scope(engine) as s:
        j1 = Job(
            canonical_key="aaaaaa01",
            source="greenhouse",
            source_id="gh-1",
            board_token="acme",
            url="https://boards.greenhouse.io/acme/jobs/1",
            title="Senior SWE",
            company="Acme",
            location="Remote, US",
            department="Eng",
            description="Backend role",
            posted_at="2026-04-20",
            employment_type="Full-time",
            track="swe",
            base_fit=0.7,
            pay_signal=0.15,
            pay_midpoint=180_000,
            loc_signal=0.0,
            freshness_signal=0.0,
            final_rank=0.85,
            us_eligible=True,
            injection_detected=False,
            status="scored",
        )
        j2 = Job(
            canonical_key="bbbbbb02",
            source="ashby",
            source_id="ashby-2",
            board_token="modal",
            url="https://jobs.ashbyhq.com/modal/abc-123/application",
            title="MTS Backend",
            company="Modal",
            location="NYC",
            department="Eng",
            description="Compute platform",
            posted_at="2026-04-25",
            employment_type="Full-time",
            track="swe",
            base_fit=0.6,
            pay_signal=0.0,
            loc_signal=0.15,
            freshness_signal=0.10,
            final_rank=0.85,
            us_eligible=True,
            injection_detected=False,
            status="scored",
        )
        j3 = Job(
            canonical_key="cccccc03",
            source="lever",
            source_id="lev-3",
            board_token="whoop",
            url="https://jobs.lever.co/whoop/3",
            title="ML Engineer",
            company="WHOOP",
            location="Boston, MA",
            department="ML",
            description="Wearables ML",
            posted_at="2026-04-18",
            employment_type="Full-time",
            track="ml",
            base_fit=0.5,
            pay_signal=0.0,
            loc_signal=0.0,
            freshness_signal=0.0,
            final_rank=0.50,
            us_eligible=True,
            injection_detected=False,
            status="scored",
        )
        s.add_all([j1, j2, j3])
        s.flush()

        a1 = Application(
            job_id=j1.id,
            track_submitted="swe",
            dry_run=False,
            outcome="ok",
            error_code="",
            error_message="",
            answers={
                "first_name": "Aadit",
                "email": "a@example.com",
                "salary_expectation": "Market rate for new-grad SWE",
                "why_company": "Generated essay text",
            },
            cover_letter_text="Dear Acme team,...",
            artifacts={
                "batch_audit": {
                    "batch_asked": ["why_company"],
                    "model_used": "gemini-2.5-flash-lite",
                },
            },
            submitted_at=datetime(2026, 4, 28, tzinfo=timezone.utc),
        )
        a2 = Application(
            job_id=j2.id,
            track_submitted="swe",
            dry_run=False,
            outcome="failed",
            error_code="no_confirmation",
            error_message=(
                "no_success_signal; final_url=https://jobs.ashbyhq.com/modal/"
                "abc-123/application"
            ),
            answers={"first_name": "Aadit", "email": "a@example.com"},
            cover_letter_text="",
            artifacts={
                "final_url": "https://jobs.ashbyhq.com/modal/abc-123/application",
                "batch_audit": {"batch_asked": [], "model_used": ""},
            },
            submitted_at=datetime(2026, 4, 30, tzinfo=timezone.utc),
        )
        s.add_all([a1, a2])
        s.flush()

        f1 = ReviewFlag(
            job_id=j2.id,
            application_id=a2.id,
            field_name="custom_uuid",
            field_label="Are you legally eligible to work in Bulgaria?",
            field_kind="select",
            required=True,
            options=["Yes", "No"],
            reason="unresolved",
            question_type=None,
            attempted_value="",
        )
        s.add(f1)

    yield engine


@pytest.fixture
def settings_temp_paths(tmp_path: Path, monkeypatch):
    """Redirect every config_writer file path into a temp dir.

    Tests that mutate config files request this fixture; tests that
    only need the seeded DB use plain ``client``.
    """
    from api.services import config_writer as cw

    paths = {
        "answer_bank": tmp_path / "answer_bank.yml",
        "companies": tmp_path / "companies.yml",
        "env": tmp_path / ".env",
        "profile": tmp_path / "profile.json",
    }
    paths["answer_bank"].write_text("salary_expectation: market rate\n")
    paths["companies"].write_text(
        "greenhouse:\n  test_safe:\n    - acme\n  live_only:\n    - openai\n"
    )
    paths["env"].write_text("DRY_RUN=true\nLOG_LEVEL=INFO\n")
    paths["profile"].write_text(
        '{"swe": {"full_name": "Aadit", "email": "a@b.c", "phone": "555",'
        ' "linkedin_url": "x", "github_url": "y",'
        ' "skills": [1,2,3], "experiences": [], "projects": [], "education": []}}'
    )
    monkeypatch.setattr(cw, "ALLOWED_PATHS", paths)
    monkeypatch.setattr(cw, "BACKUP_DIR", tmp_path / "backups")
    yield paths


@pytest.fixture
def client(seeded_engine):
    """TestClient with the production engine overridden."""
    app.dependency_overrides[get_engine] = lambda: seeded_engine
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_engine, None)
