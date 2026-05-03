"""Subprocess orchestration for pipeline triggers + retries.

We deliberately shell out to ``autoapply <subcommand>`` and the
``scripts/apply_by_job_ids.py`` script rather than calling the
internals directly. Two reasons:

1. We get the full CLI's ``INFO ...`` log lines for SSE streaming.
2. The CLI's commit-on-success / rollback-on-exception semantics
   are already battle-tested. Reproducing them in-process is risky.

A "run" is identified by a short uuid7-ish id and stored as:

    api/runs/<run_id>.log         — captured stdout+stderr
    api/runs/<run_id>.meta.json   — RunMeta (stage, status, summary, …)

Run history is just ``ls api/runs/`` + parse the .meta.json files.
The directory is gitignored.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from api.schemas import RunMeta


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = PROJECT_ROOT / "api" / "runs"
RUNS_DIR.mkdir(parents=True, exist_ok=True)


# ── Stages → CLI commands ───────────────────────────────────────────


def _build_cmd(
    stage: str,
    *,
    sources: list[str] | None = None,
    boards: list[str] | None = None,
    limit: int | None = None,
    min_rank: float | None = None,
    dry_run: bool | None = None,
    job_ids: list[int] | None = None,
) -> list[str]:
    """Build the argv for the subprocess. Validates the stage."""
    cmd: list[str] = []
    if stage == "ingest":
        cmd = ["autoapply", "ingest"]
        if sources:
            for s in sources:
                cmd += ["--source", s]
        if boards:
            for b in boards:
                cmd += ["--board", b]
        if limit is not None:
            cmd += ["--limit", str(limit)]
    elif stage == "score":
        cmd = ["autoapply", "score"]
        if limit is not None:
            cmd += ["--limit", str(limit)]
    elif stage == "apply":
        cmd = ["autoapply", "apply"]
        if limit is not None:
            cmd += ["--limit", str(limit)]
        if min_rank is not None:
            cmd += ["--min-rank", str(min_rank)]
        if dry_run is False:
            cmd += ["--no-dry-run"]
    elif stage == "profile-build":
        cmd = ["autoapply", "profile-build"]
    elif stage == "apply-by-ids":
        if not job_ids:
            raise ValueError("apply-by-ids requires job_ids")
        cmd = ["python", "scripts/apply_by_job_ids.py"]
        cmd += [str(i) for i in job_ids]
        if dry_run is False:
            cmd += ["--no-dry-run"]
    else:
        raise ValueError(f"unknown stage: {stage!r}")
    return cmd


# ── Run lifecycle ────────────────────────────────────────────────────


def _new_run_id() -> str:
    """Short, monotonic-ish ID. ``uuid7`` would be ideal; uuid4 + ts works."""
    ts = int(time.time())
    return f"{ts:x}{uuid.uuid4().hex[:6]}"


def _write_meta(run_id: str, meta: dict[str, Any]) -> None:
    p = RUNS_DIR / f"{run_id}.meta.json"
    p.write_text(json.dumps(meta, default=str), encoding="utf-8")


def _read_meta(run_id: str) -> dict[str, Any] | None:
    p = RUNS_DIR / f"{run_id}.meta.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _summary_from_log(log_text: str) -> str:
    """Pull the last informative line out of the captured log.

    The CLI emits lines like ``score done — total=16 ok=12 ...`` or
    ``ingest done — seen=100 inserted=0 skipped=100``. We grep the
    last 5KB for one of those done lines; fallback is the final
    non-empty line truncated.
    """
    if not log_text:
        return ""
    tail = log_text[-5_000:]
    for line in reversed(tail.splitlines()):
        if " done — " in line or "done — " in line:
            return line.strip().split(" — ")[-1][:200]
    for line in reversed(tail.splitlines()):
        if line.strip():
            return line.strip()[:200]
    return ""


async def start_run(
    stage: str,
    *,
    sources: list[str] | None = None,
    boards: list[str] | None = None,
    limit: int | None = None,
    min_rank: float | None = None,
    dry_run: bool | None = None,
    job_ids: list[int] | None = None,
) -> str:
    """Spawn the subprocess and persist initial metadata.

    Returns the ``run_id``. Caller streams logs via :func:`tail_run`
    (SSE) or polls :func:`get_run`.
    """
    cmd = _build_cmd(
        stage,
        sources=sources,
        boards=boards,
        limit=limit,
        min_rank=min_rank,
        dry_run=dry_run,
        job_ids=job_ids,
    )
    run_id = _new_run_id()
    log_path = RUNS_DIR / f"{run_id}.log"

    started_at = datetime.now(timezone.utc)
    _write_meta(
        run_id,
        {
            "run_id": run_id,
            "stage": stage,
            "status": "running",
            "started_at": started_at.isoformat(),
            "finished_at": None,
            "duration_ms": None,
            "summary": "",
            "cmd": " ".join(cmd),
            "log_size": 0,
        },
    )

    async def _runner() -> None:
        log_fp = log_path.open("w", encoding="utf-8")
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(PROJECT_ROOT),
                env={**os.environ},
            )
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace")
                log_fp.write(decoded)
                log_fp.flush()
            rc = await proc.wait()
        except Exception as exc:  # pragma: no cover — surfaced in meta
            log_fp.write(f"\n[runner] subprocess error: {exc}\n")
            rc = -1
        finally:
            log_fp.close()

        finished_at = datetime.now(timezone.utc)
        log_text = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        _write_meta(
            run_id,
            {
                "run_id": run_id,
                "stage": stage,
                "status": "ok" if rc == 0 else "failed",
                "started_at": started_at.isoformat(),
                "finished_at": finished_at.isoformat(),
                "duration_ms": int(
                    (finished_at - started_at).total_seconds() * 1000
                ),
                "summary": _summary_from_log(log_text),
                "cmd": " ".join(cmd),
                "log_size": len(log_text),
            },
        )

    # Fire-and-forget; we don't await.
    asyncio.create_task(_runner())
    return run_id


def get_run(run_id: str) -> RunMeta | None:
    """Return the latest persisted metadata for a run."""
    meta = _read_meta(run_id)
    if not meta:
        return None
    log_path = RUNS_DIR / f"{run_id}.log"
    log_size = log_path.stat().st_size if log_path.exists() else 0
    return RunMeta(
        run_id=meta["run_id"],
        stage=meta["stage"],
        status=meta["status"],
        started_at=meta["started_at"],
        finished_at=meta.get("finished_at"),
        duration_ms=meta.get("duration_ms"),
        summary=meta.get("summary", ""),
        cmd=meta.get("cmd", ""),
        log_size=log_size,
    )


def list_runs(limit: int = 50) -> list[RunMeta]:
    """Most-recent runs first. Cheap; the directory holds tens-of-rows."""
    metas: list[tuple[float, dict[str, Any]]] = []
    for p in RUNS_DIR.glob("*.meta.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        metas.append((p.stat().st_mtime, data))
    metas.sort(key=lambda t: t[0], reverse=True)

    out: list[RunMeta] = []
    for _, m in metas[:limit]:
        log_path = RUNS_DIR / f"{m['run_id']}.log"
        log_size = log_path.stat().st_size if log_path.exists() else 0
        out.append(
            RunMeta(
                run_id=m["run_id"],
                stage=m["stage"],
                status=m["status"],
                started_at=m["started_at"],
                finished_at=m.get("finished_at"),
                duration_ms=m.get("duration_ms"),
                summary=m.get("summary", ""),
                cmd=m.get("cmd", ""),
                log_size=log_size,
            )
        )
    return out


async def tail_run(run_id: str) -> AsyncIterator[str]:
    """Async generator yielding new log lines until the run ends.

    Used by the SSE endpoint. Polls the file every 250ms; cheap and
    matches uvicorn's default keep-alive cadence comfortably.
    """
    log_path = RUNS_DIR / f"{run_id}.log"

    # Wait briefly for the file to appear.
    for _ in range(40):
        if log_path.exists():
            break
        await asyncio.sleep(0.05)

    if not log_path.exists():
        yield "[runner] log file never appeared\n"
        return

    pos = 0
    while True:
        try:
            with log_path.open("r", encoding="utf-8") as fp:
                fp.seek(pos)
                chunk = fp.read()
                pos = fp.tell()
        except FileNotFoundError:
            break
        if chunk:
            for line in chunk.splitlines(keepends=True):
                yield line

        meta = _read_meta(run_id)
        if meta and meta["status"] != "running":
            # Drain any straggler lines flushed after status flip.
            await asyncio.sleep(0.1)
            with log_path.open("r", encoding="utf-8") as fp:
                fp.seek(pos)
                final_chunk = fp.read()
            if final_chunk:
                for line in final_chunk.splitlines(keepends=True):
                    yield line
            return

        await asyncio.sleep(0.25)
