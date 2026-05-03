"""HTTP-level tests for Phase 3 endpoints (retry / batch / pipeline)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from api.services import pipeline_runner


@pytest.fixture
def fake_run(monkeypatch):
    """Replace ``pipeline_runner.start_run`` with an AsyncMock that
    returns a deterministic run_id without spawning a subprocess."""
    mock = AsyncMock(return_value="r-fake-001")
    monkeypatch.setattr(pipeline_runner, "start_run", mock)
    return mock


def test_pipeline_trigger_unknown_stage(client):
    r = client.post("/api/pipeline/borked", json={})
    assert r.status_code == 400
    assert "unknown stage" in r.json()["detail"]


def test_pipeline_trigger_ingest(client, fake_run):
    r = client.post(
        "/api/pipeline/ingest",
        json={"sources": ["ashby"], "limit": 10},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["run_id"] == "r-fake-001"
    fake_run.assert_awaited_once()
    assert fake_run.await_args.kwargs["sources"] == ["ashby"]
    assert fake_run.await_args.kwargs["limit"] == 10


def test_pipeline_trigger_apply_no_dry_run(client, fake_run):
    r = client.post(
        "/api/pipeline/apply",
        json={"limit": 3, "dry_run": False},
    )
    assert r.status_code == 200
    assert fake_run.await_args.kwargs["dry_run"] is False


def test_pipeline_runs_list_empty(tmp_path, client, monkeypatch):
    """Empty runs dir → empty list."""
    monkeypatch.setattr(pipeline_runner, "RUNS_DIR", tmp_path)
    r = client.get("/api/pipeline/runs")
    assert r.status_code == 200
    assert r.json() == []


def test_pipeline_run_detail_404(client):
    r = client.get("/api/pipeline/runs/nope")
    assert r.status_code == 404


def test_application_retry(client, fake_run):
    # Find any seeded app id
    apps = client.get("/api/applications").json()["items"]
    assert apps, "seed must include applications"
    app_id = apps[0]["id"]
    r = client.post(f"/api/applications/{app_id}/retry")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "r-fake-001" in body["message"]
    fake_run.assert_awaited_once()
    # First positional arg should be "apply-by-ids"
    assert fake_run.await_args.args[0] == "apply-by-ids"


def test_application_batch(client, fake_run):
    r = client.post(
        "/api/applications/batch",
        json={"job_ids": [1, 2], "dry_run": True},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["started"] == 2
    assert body["run_id"] == "r-fake-001"


def test_application_batch_empty(client, fake_run):
    r = client.post("/api/applications/batch", json={"job_ids": []})
    assert r.status_code == 400


def test_application_retry_404(client, fake_run):
    r = client.post("/api/applications/99999/retry")
    assert r.status_code == 404


def test_review_spam_archive(client):
    spam = client.get("/api/review/spam").json()
    assert spam, "seed must produce a spam reject"
    app_id = spam[0]["application_id"]
    r = client.post(f"/api/review/spam/{app_id}/archive")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    # Job should now be archived
    jobs = client.get("/api/jobs", params={"company": spam[0]["job_company"]}).json()
    statuses = {it["status"] for it in jobs["items"]}
    assert "archived" in statuses


def test_review_flag_resolve(client, fake_run, settings_temp_paths):
    flags = client.get("/api/review/flags").json()["items"]
    assert flags, "seed must include a review flag"
    flag_id = flags[0]["id"]

    r = client.post(
        f"/api/review/flags/{flag_id}/resolve",
        json={"value": "Yes", "bank_key": "eligible_bulgaria"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["bank_key_written"] == "eligible_bulgaria"
    assert body["retry_run_id"] == "r-fake-001"

    # Bank file should now contain the new key.
    text = settings_temp_paths["answer_bank"].read_text()
    assert "eligible_bulgaria" in text
    assert "Yes" in text

    # Flag should be deleted.
    after = client.get("/api/review/flags").json()
    assert all(f["id"] != flag_id for f in after["items"])
