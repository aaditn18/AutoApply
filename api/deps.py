"""FastAPI dependency providers.

The backend lazy-binds to whichever SQLAlchemy engine the host
process resolves. In production that's the on-disk
``state/jobs.sqlite``; in tests we override
:func:`get_engine` to return an in-memory engine seeded by the
fixture.

Two stable shapes:

- :func:`get_engine` — module-cached :class:`sqlalchemy.Engine`.
  Override with ``app.dependency_overrides`` in tests.
- :func:`get_db` — yields a :class:`sqlalchemy.orm.Session`. Wraps
  the existing ``session_scope`` so we keep the project's "commit on
  success / rollback on exception" invariant.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from autoapply.config import Settings, get_settings
from autoapply.tracker.db import create_engine_from_settings, session_scope


_engine: Engine | None = None


def get_engine() -> Engine:
    """Return a module-cached engine pinned to the production SQLite file.

    Tests substitute via:

        app.dependency_overrides[get_engine] = lambda: my_test_engine
    """
    global _engine
    if _engine is None:
        _engine = create_engine_from_settings()
    return _engine


def get_db(engine: Engine = Depends(get_engine)) -> Iterator[Session]:
    """Yield a transactional session. Rolled back on exception."""
    with session_scope(engine) as session:
        yield session


def get_app_settings() -> Settings:
    """Expose the project's :class:`Settings` to routers that need it
    (e.g., ``/api/env`` writes back to the same .env path)."""
    return get_settings()
