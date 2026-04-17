"""SQLAlchemy engine + session plumbing.

For the MVP we use SQLite with WAL mode; the DB file lives at
`state/jobs.sqlite` and is committed to the AutoApply repo via the
pipeline workflow. For tests we spin up an in-memory engine.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from autoapply.config import Settings, get_settings
from autoapply.tracker.models import Base


def _install_sqlite_pragmas(engine: Engine) -> None:
    """Turn on WAL + foreign keys on every new connection.

    WAL gives us concurrent read while a writer holds the lock — useful
    because the pipeline workflow runs ingest, score, and apply in the
    same process, and nightly may touch the DB in parallel.
    """

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _record):  # type: ignore[no-untyped-def]
        cur = dbapi_conn.cursor()
        try:
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA synchronous=NORMAL")
        finally:
            cur.close()


def create_engine_from_settings(settings: Settings | None = None) -> Engine:
    """Build an Engine targeting the real SQLite file."""
    s = settings or get_settings()
    # Ensure state dir exists so sqlite can create the file.
    Path(s.state_dir).mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        s.database_url,
        future=True,
        echo=False,
        connect_args={"check_same_thread": False},
    )
    _install_sqlite_pragmas(engine)
    return engine


def create_memory_engine() -> Engine:
    """In-memory SQLite engine for tests. Foreign keys ON, no WAL."""
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        echo=False,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _record):  # type: ignore[no-untyped-def]
        cur = dbapi_conn.cursor()
        try:
            cur.execute("PRAGMA foreign_keys=ON")
        finally:
            cur.close()

    Base.metadata.create_all(engine)
    return engine


def init_db(engine: Engine) -> None:
    """Create all tables. MVP shortcut — prod uses Alembic."""
    Base.metadata.create_all(engine)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """Transactional scope. Commits on success, rolls back on exception."""
    factory = make_session_factory(engine)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
