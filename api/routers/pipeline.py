"""Pipeline triggers + run history + SSE log streaming.

  POST /api/pipeline/{stage}      → kicks off a subprocess, returns run_id
  GET  /api/pipeline/runs         → recent runs (newest first)
  GET  /api/pipeline/runs/{id}    → single run metadata
  GET  /api/pipeline/runs/{id}/logs → SSE log stream (text/event-stream)
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from api.schemas import PipelineTriggerIn, RunMeta
from api.services import pipeline_runner


router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


_STAGE_OK = {"ingest", "score", "apply", "profile-build"}


@router.post("/{stage}", response_model=dict)
async def trigger_stage(stage: str, body: PipelineTriggerIn | None = None):
    if stage not in _STAGE_OK:
        raise HTTPException(status_code=400, detail=f"unknown stage {stage!r}")
    body = body or PipelineTriggerIn()
    try:
        run_id = await pipeline_runner.start_run(
            stage,
            sources=body.sources,
            boards=body.boards,
            limit=body.limit,
            min_rank=body.min_rank,
            dry_run=body.dry_run,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"run_id": run_id, "stage": stage}


@router.get("/runs", response_model=list[RunMeta])
def list_runs_route(limit: int = 50) -> list[RunMeta]:
    return pipeline_runner.list_runs(limit=limit)


@router.get("/runs/{run_id}", response_model=RunMeta)
def get_run_route(run_id: str) -> RunMeta:
    out = pipeline_runner.get_run(run_id)
    if out is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return out


@router.get("/runs/{run_id}/logs")
async def stream_logs(run_id: str):
    """SSE stream — each ``data:`` event is one log line.

    Client side uses ``new EventSource(url)`` and listens for the
    default ``message`` event. The stream closes when the subprocess
    exits and the runner writes the terminal status.
    """
    if pipeline_runner.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")

    async def _events():
        async for line in pipeline_runner.tail_run(run_id):
            yield {"event": "message", "data": line.rstrip("\n")}
        yield {"event": "end", "data": ""}

    return EventSourceResponse(_events())
