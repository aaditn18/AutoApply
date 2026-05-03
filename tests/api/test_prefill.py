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
    """When ``track`` is None, the path uses 'swe' regardless of which
    PDFs exist on disk — the function builds the path string by
    interpolating the track name and only branches to the fallback
    when the chosen PDF is missing AND swe.pdf exists."""
    p = prefill_service._resume_path_for_track(None)
    assert p.endswith("aadit_nilay_resume_swe.pdf")


def test_resume_path_uses_track_when_pdf_exists(tmp_path, monkeypatch):
    """The CI runner doesn't have the resumes submodule checked out,
    so we point RESUMES_DIR at a tmp dir + create the expected PDFs
    before asserting the lookup honors the track."""
    monkeypatch.setattr(prefill_service, "RESUMES_DIR", tmp_path)
    (tmp_path / "aadit_nilay_resume_ml.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "aadit_nilay_resume_swe.pdf").write_bytes(b"%PDF-1.4")

    p = prefill_service._resume_path_for_track("ml")
    assert p.endswith("aadit_nilay_resume_ml.pdf")


def test_resume_path_falls_back_to_swe_when_track_pdf_missing(tmp_path, monkeypatch):
    """Track-specific PDF missing → fall back to swe.pdf."""
    monkeypatch.setattr(prefill_service, "RESUMES_DIR", tmp_path)
    # Only swe.pdf exists; asking for 'quant' should fall back.
    (tmp_path / "aadit_nilay_resume_swe.pdf").write_bytes(b"%PDF-1.4")

    p = prefill_service._resume_path_for_track("quant")
    assert p.endswith("aadit_nilay_resume_swe.pdf")


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


def test_build_payload_handles_missing_cover_letter(tmp_path, monkeypatch):
    """No cover letter text → no `cover_letter` key in data. Resume
    path always set; we point RESUMES_DIR at tmp + create the ml.pdf
    so the assertion holds on the CI runner too (where the resumes
    submodule isn't checked out)."""
    monkeypatch.setattr(prefill_service, "RESUMES_DIR", tmp_path)
    (tmp_path / "aadit_nilay_resume_ml.pdf").write_bytes(b"%PDF-1.4")

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


def test_run_prefill_passes_apply_url_for_greenhouse(tmp_path, monkeypatch):
    """Regression: Lyft (and other Greenhouse customers on custom apply
    domains like careerpuck.com) must navigate to ``Job.url`` from the
    public feed, NOT the constructed ``boards.greenhouse.io/<token>/jobs/<id>``
    path. The constructed path 404s or serves a stripped form whose
    selectors don't match production."""
    monkeypatch.setattr(prefill_service, "RESUMES_DIR", tmp_path)
    (tmp_path / "aadit_nilay_resume_swe.pdf").write_bytes(b"%PDF-1.4")

    custom_url = (
        "https://app.careerpuck.com/job-board/lyft/job/8477773002"
        "?gh_jid=8477773002"
    )
    snap = prefill_service._DetachedJob.__new__(prefill_service._DetachedJob)
    snap.id = 4897
    snap.source = "greenhouse"
    snap.board_token = "lyft"
    snap.source_id = "8477773002"
    snap.url = custom_url
    snap.title = "Senior Data Scientist, Decisions - Risk"
    snap.company = "Lyft"
    snap.track = "ml"

    captured: dict[str, object] = {}

    def fake_submit_greenhouse(**kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(
        "autoapply.execute.playwright_submit.submit_greenhouse",
        fake_submit_greenhouse,
    )

    prefill_service._run_prefill_in_thread(
        source="greenhouse",
        job_snapshot=snap,
        track="ml",
        data={"first_name": "Aadit"},
        files={"resume": str(tmp_path / "aadit_nilay_resume_swe.pdf")},
    )

    assert captured["apply_url"] == custom_url
    assert captured["board_token"] == "lyft"
    assert captured["stop_before_submit"] is True
    assert captured["headless"] is False


def test_run_prefill_passes_apply_url_for_lever(tmp_path, monkeypatch):
    """Same idea for Lever customers on custom subdomains."""
    monkeypatch.setattr(prefill_service, "RESUMES_DIR", tmp_path)
    (tmp_path / "aadit_nilay_resume_swe.pdf").write_bytes(b"%PDF-1.4")

    custom_url = "https://boards.eu.lever.co/example/abc-123/apply"
    snap = prefill_service._DetachedJob.__new__(prefill_service._DetachedJob)
    snap.id = 1
    snap.source = "lever"
    snap.board_token = "example"
    snap.source_id = "abc-123"
    snap.url = custom_url
    snap.title = "t"
    snap.company = "Example"
    snap.track = "swe"

    captured: dict[str, object] = {}

    def fake_submit_lever(**kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(
        "autoapply.execute.playwright_submit.submit_lever",
        fake_submit_lever,
    )

    prefill_service._run_prefill_in_thread(
        source="lever",
        job_snapshot=snap,
        track="swe",
        data={},
        files={"resume": str(tmp_path / "aadit_nilay_resume_swe.pdf")},
    )

    assert captured["apply_url"] == custom_url


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
