"""Tests for the prefill route + service.

The actual playwright thread is stubbed out — we verify the request
parses, the payload is built correctly from the stored Application
row, and the endpoint returns 200 with the expected metadata. We
never spawn a real browser in the test suite.
"""

from __future__ import annotations

from unittest.mock import patch

from autoapply.tracker.db import session_scope
from autoapply.tracker.models import Application, Job

from api.services import prefill_service


def test_resume_path_falls_back_when_track_missing():
    p = prefill_service._resume_path_for_track(None)
    assert p.endswith("aadit_nilay_resume_swe.pdf")


def test_resume_path_uses_track():
    p = prefill_service._resume_path_for_track("ml")
    assert p.endswith("aadit_nilay_resume_ml.pdf")


def test_build_payload_strips_path_keys_and_injects_cover_letter():
    """Ensures we don't double-up resume in data, and that
    cover_letter_text gets placed back into the data dict so the
    submitter's textarea fill picks it up."""
    a = Application(
        id=1,
        job_id=1,
        track_submitted="swe",
        dry_run=False,
        outcome="failed",
        answers={
            "first_name": "Aadit",
            "email": "a@b.c",
            # Path-shaped values for resume/cover_letter — get dropped.
            "resume": "/some/path/aadit_nilay_resume_swe.pdf",
            "cover_letter": "/old/path/cover.pdf",
            "salary_expectation": "Market rate",
        },
        cover_letter_text="Dear team, I'm interested in the role…",
    )

    data, files = prefill_service._build_payload(a)

    assert data["first_name"] == "Aadit"
    assert data["email"] == "a@b.c"
    assert data["salary_expectation"] == "Market rate"
    # Old path values dropped; cover letter rewritten as text.
    assert data["cover_letter"] == "Dear team, I'm interested in the role…"
    assert "/old/path" not in str(data.get("cover_letter", ""))
    # files holds the per-track resume PDF, not the path from answers.
    assert files["resume"].endswith("aadit_nilay_resume_swe.pdf")


def test_build_payload_handles_missing_cover_letter():
    a = Application(
        id=1,
        job_id=1,
        track_submitted="ml",
        dry_run=False,
        outcome="captcha",
        answers={"first_name": "Aadit"},
        cover_letter_text="",
    )
    data, files = prefill_service._build_payload(a)
    assert "cover_letter" not in data
    assert files["resume"].endswith("aadit_nilay_resume_ml.pdf")


def test_prefill_endpoint_404_for_unknown_app(client):
    r = client.post("/api/applications/99999/prefill")
    assert r.status_code == 404
    assert "not found" in r.json()["detail"]


def test_prefill_endpoint_400_when_no_answers(client, seeded_engine):
    """An application row with empty answers can't be replayed."""
    with session_scope(seeded_engine) as s:
        # Acme job already has an app from the seed; create one with
        # empty answers on the WHOOP job.
        job = (
            s.query(Job).filter(Job.canonical_key == "cccccc03").one()
        )
        empty_app = Application(
            job_id=job.id,
            track_submitted="ml",
            dry_run=False,
            outcome="failed",
            answers={},
            cover_letter_text="",
        )
        s.add(empty_app)
        s.flush()
        empty_app_id = empty_app.id

    r = client.post(f"/api/applications/{empty_app_id}/prefill")
    assert r.status_code == 400
    assert "no stored answers" in r.json()["detail"]


def test_prefill_endpoint_starts_thread_and_returns_metadata(client, seeded_engine):
    """Happy path. Threading.Thread is patched so we don't actually
    launch a browser; we just verify the call was queued with the
    right payload."""
    # Find Acme's seeded application id (has answers + cover_letter).
    apps = client.get(
        "/api/applications", params={"outcome": "ok"}
    ).json()["items"]
    app_id = apps[0]["id"]

    with patch("api.services.prefill_service.threading.Thread") as Thread:
        # Stop the thread from actually starting a worker.
        Thread.return_value.start.return_value = None
        r = client.post(f"/api/applications/{app_id}/prefill")

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["application_id"] == app_id
    assert body["source"] == "greenhouse"
    assert body["fields_count"] >= 3  # at least name+email+salary+why
    assert body["resume_path"].endswith(".pdf")

    # Thread was constructed with daemon=True and the correct kwargs
    Thread.assert_called_once()
    call_kwargs = Thread.call_args.kwargs
    assert call_kwargs["daemon"] is True
    assert call_kwargs["target"] is prefill_service._run_prefill_in_thread
    inner = call_kwargs["kwargs"]
    assert inner["source"] == "greenhouse"
    assert "resume" in inner["files"]
    # _DetachedJob snapshot was built — has the URL we'd navigate to
    assert inner["job_snapshot"].url.endswith("/jobs/1")
    # Track propagated for llm_context loading
    assert inner["track"] in ("swe", "ml", "hpc", "quant")


def test_prefill_endpoint_includes_review_outcome(client, seeded_engine):
    """Review-outcome applications should also be prefill-eligible."""
    with session_scope(seeded_engine) as s:
        job = s.query(Job).filter(Job.canonical_key == "bbbbbb02").one()
        review_app = Application(
            job_id=job.id,
            track_submitted="swe",
            dry_run=False,
            outcome="review",
            answers={"first_name": "Aadit", "email": "a@b.c"},
            cover_letter_text="Cover letter body",
        )
        s.add(review_app)
        s.flush()
        review_app_id = review_app.id

    with patch("api.services.prefill_service.threading.Thread") as Thread:
        Thread.return_value.start.return_value = None
        r = client.post(f"/api/applications/{review_app_id}/prefill")

    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_detached_job_snapshots_required_fields():
    """_DetachedJob must capture every column the worker thread reads,
    so the worker doesn't blow up after the request session closes."""
    job = Job(
        id=42,
        canonical_key="xxx",
        source="ashby",
        source_id="ashby-42",
        board_token="modal",
        url="https://jobs.ashbyhq.com/modal/abc/application",
        title="MTS",
        company="Modal",
        track="swe",
    )
    snap = prefill_service._DetachedJob(job)
    assert snap.id == 42
    assert snap.source == "ashby"
    assert snap.url.endswith("/application")
    assert snap.company == "Modal"
    assert snap.track == "swe"
