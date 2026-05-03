"""AutoApply local web app — FastAPI backend.

Mounted on http://127.0.0.1:8765. Read-only views over the existing
SQLAlchemy models + write endpoints for retries, config edits, and
pipeline triggers.

Module layout:
  api.main      — FastAPI factory, CORS, routers, static mounts
  api.deps      — DB session + settings dependency injection
  api.schemas   — Pydantic API schemas (separate from ORM models)
  api.routers.* — one module per resource
  api.services.* — query helpers + business logic the routers call

Backend NEVER bypasses ``Applicator.apply()`` or ``session_scope`` —
this is the codified invariant from the plan.
"""

__version__ = "0.1.0"
