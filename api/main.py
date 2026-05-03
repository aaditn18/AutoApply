"""FastAPI app factory + entry-point.

Run locally:

    uvicorn api.main:app --reload --port 8765

Or via the Makefile:

    make api

The app binds 127.0.0.1 by default — see ``scripts/dev.sh`` for the
combined backend + frontend run.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api import __version__
from api.routers import (
    answer_bank,
    applications,
    companies,
    dashboard,
    env,
    jobs,
    llm_audit,
    pipeline,
    profile,
    resumes,
    review,
)


log = logging.getLogger("api")


def create_app() -> FastAPI:
    """Build the FastAPI application."""
    app = FastAPI(
        title="AutoApply local API",
        version=__version__,
        # OpenAPI schema is exposed for the typegen pipeline
        # (`make typegen` runs `npx openapi-typescript`).
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
    )

    # Frontend (Next.js) lives on a different port during dev. CORS
    # is locked down to localhost — this app is single-user, never
    # exposed beyond 127.0.0.1.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3765",
            "http://127.0.0.1:3765",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    # Pre-submit screenshots are reused as-is from the existing
    # submitter pipeline at state/failed_submits/. Static-mount the
    # directory; URL hash maps via _save_presubmit_screenshot.
    screenshots_dir = Path("state/failed_submits")
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/api/static/screenshots",
        StaticFiles(directory=str(screenshots_dir)),
        name="screenshots",
    )

    # ── Routers ─────────────────────────────────────────────────
    app.include_router(dashboard.router)
    app.include_router(jobs.router)
    app.include_router(applications.router)
    app.include_router(review.router)
    # Phase 2: settings
    app.include_router(profile.router)
    app.include_router(answer_bank.router)
    app.include_router(companies.router)
    app.include_router(env.router)
    # Phase 3: pipeline triggers
    app.include_router(pipeline.router)
    # Phase 4: resumes + LLM audit
    app.include_router(resumes.router)
    app.include_router(llm_audit.router)

    # Health check — used by the `make dev` orchestrator + tests.
    @app.get("/api/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
