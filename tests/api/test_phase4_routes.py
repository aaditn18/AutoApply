"""Tests for Phase 4 endpoints (resumes, llm_audit, dashboard.velocity)."""

from __future__ import annotations

from datetime import datetime, timezone

from autoapply.tracker.db import session_scope
from autoapply.tracker.models import Application, Job


def test_resumes_route_returns_4_tracks(client):
    """Even with no PDFs in tmp, the route shows the 4 expected tracks."""
    r = client.get("/api/resumes")
    assert r.status_code == 200
    body = r.json()
    tracks = {it["track"] for it in body}
    assert tracks == {"swe", "ml", "hpc", "quant"}


def test_llm_audit_filters_to_apps_with_model(client):
    r = client.get("/api/llm_audit")
    body = r.json()
    # The seed has one app with model_used="gemini-2.5-flash-lite".
    assert len(body) == 1
    assert body[0]["model_used"] == "gemini-2.5-flash-lite"
    assert body[0]["batch_asked_count"] == 1


def test_llm_audit_skips_apps_without_model(client, seeded_engine):
    # Add another Application with no model_used → must NOT show up.
    with session_scope(seeded_engine) as s:
        j = Job(
            canonical_key="dddddd04",
            source="ashby",
            source_id="ashby-4",
            board_token="x",
            url="https://example.com",
            title="t",
            company="X",
        )
        s.add(j)
        s.flush()
        s.add(
            Application(
                job_id=j.id,
                track_submitted="swe",
                dry_run=True,
                outcome="dry_run",
                answers={},
                cover_letter_text="",
                artifacts={"batch_audit": {"model_used": "", "batch_asked": []}},
                submitted_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
            )
        )

    r = client.get("/api/llm_audit")
    assert len(r.json()) == 1  # the no-model row was skipped


def test_dashboard_velocity_has_15_buckets(client):
    """velocity = last 14 days inclusive → 15 entries."""
    r = client.get("/api/dashboard")
    body = r.json()
    assert "velocity" in body
    assert len(body["velocity"]) == 15
    keys_per_point = set(body["velocity"][0].keys())
    assert keys_per_point >= {"date", "apps", "ok", "failed"}
