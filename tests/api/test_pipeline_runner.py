"""Hermetic tests for the pipeline_runner service.

We exercise the subprocess machinery with a stand-in command (``echo``
+ ``true`` / ``false``) so we don't depend on the real ``autoapply``
CLI being importable in the test process.
"""

from __future__ import annotations

import asyncio

import pytest

from api.services import pipeline_runner


@pytest.fixture(autouse=True)
def _redirect_runs_dir(tmp_path, monkeypatch):
    """Each test gets its own runs/ dir."""
    monkeypatch.setattr(pipeline_runner, "RUNS_DIR", tmp_path)
    yield


# ── _build_cmd ───────────────────────────────────────────────────────


def test_build_cmd_ingest():
    cmd = pipeline_runner._build_cmd(
        "ingest", sources=["ashby"], boards=["modal", "mux"], limit=10
    )
    assert cmd == [
        "autoapply", "ingest",
        "--source", "ashby",
        "--board", "modal", "--board", "mux",
        "--limit", "10",
    ]


def test_build_cmd_score():
    cmd = pipeline_runner._build_cmd("score", limit=20)
    assert cmd == ["autoapply", "score", "--limit", "20"]


def test_build_cmd_apply_with_no_dry_run():
    cmd = pipeline_runner._build_cmd("apply", limit=3, dry_run=False)
    assert cmd == ["autoapply", "apply", "--limit", "3", "--no-dry-run"]


def test_build_cmd_apply_default_dry_run():
    """Dry-run defaults to True at the CLI; no --no-dry-run flag."""
    cmd = pipeline_runner._build_cmd("apply", limit=3)
    assert "--no-dry-run" not in cmd


def test_build_cmd_apply_by_ids_requires_ids():
    with pytest.raises(ValueError, match="job_ids"):
        pipeline_runner._build_cmd("apply-by-ids")


def test_build_cmd_apply_by_ids():
    cmd = pipeline_runner._build_cmd(
        "apply-by-ids", job_ids=[1, 2, 3], dry_run=False
    )
    assert cmd[0] == "python"
    assert "scripts/apply_by_job_ids.py" in cmd[1]
    assert cmd[2:5] == ["1", "2", "3"]
    assert "--no-dry-run" in cmd


def test_build_cmd_unknown_stage():
    with pytest.raises(ValueError, match="unknown stage"):
        pipeline_runner._build_cmd("nope")


# ── _summary_from_log ────────────────────────────────────────────────


def test_summary_from_done_line():
    log = "12:00:00 INFO: starting\n13:00:00 INFO score done — total=16 ok=12\n"
    assert pipeline_runner._summary_from_log(log) == "total=16 ok=12"


def test_summary_falls_back_to_last_line():
    log = "an error line\nyet another\n"
    assert pipeline_runner._summary_from_log(log) == "yet another"


def test_summary_empty():
    assert pipeline_runner._summary_from_log("") == ""


# ── start_run + tail_run with a stub command ────────────────────────


@pytest.mark.asyncio
async def test_start_run_records_meta_and_log(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_runner, "RUNS_DIR", tmp_path)

    # Replace _build_cmd so we don't depend on the autoapply CLI.
    monkeypatch.setattr(
        pipeline_runner,
        "_build_cmd",
        lambda *a, **k: ["bash", "-c", "echo hello && echo world"],
    )

    run_id = await pipeline_runner.start_run("ingest")
    # Wait for the runner to finish — bounded poll.
    for _ in range(40):
        meta = pipeline_runner.get_run(run_id)
        if meta and meta.status != "running":
            break
        await asyncio.sleep(0.1)
    meta = pipeline_runner.get_run(run_id)
    assert meta is not None
    assert meta.status == "ok"
    log = (tmp_path / f"{run_id}.log").read_text()
    assert "hello" in log
    assert "world" in log


@pytest.mark.asyncio
async def test_start_run_failure_marks_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_runner, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(
        pipeline_runner,
        "_build_cmd",
        lambda *a, **k: ["bash", "-c", "exit 7"],
    )
    run_id = await pipeline_runner.start_run("score")
    for _ in range(40):
        meta = pipeline_runner.get_run(run_id)
        if meta and meta.status != "running":
            break
        await asyncio.sleep(0.1)
    meta = pipeline_runner.get_run(run_id)
    assert meta is not None and meta.status == "failed"


@pytest.mark.asyncio
async def test_list_runs_orders_newest_first(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_runner, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(
        pipeline_runner,
        "_build_cmd",
        lambda *a, **k: ["bash", "-c", "true"],
    )

    first = await pipeline_runner.start_run("ingest")
    await asyncio.sleep(0.2)
    second = await pipeline_runner.start_run("score")

    # Wait for both to finish
    for _ in range(40):
        a, b = pipeline_runner.get_run(first), pipeline_runner.get_run(second)
        if a and a.status != "running" and b and b.status != "running":
            break
        await asyncio.sleep(0.1)

    runs = pipeline_runner.list_runs()
    assert len(runs) == 2
    # Newest first
    assert runs[0].run_id == second
    assert runs[1].run_id == first
