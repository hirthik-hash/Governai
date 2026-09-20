# backend/database/session.py

"""
Engine and session plumbing (Day 71).

Nothing here runs at import time - no engine is created and no
database file is touched until make_engine() is called, so importing
this module (or running the test suite) never creates a stray .db file.
"""

from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.config import settings
from database.models import Base

_IN_MEMORY_SQLITE_URLS = ("sqlite://", "sqlite:///:memory:")


def make_engine(url: Optional[str] = None) -> Engine:
    """
    Builds an Engine for `url` (default: settings.database_url).

    SQLite needs three things a server database does not:
      - check_same_thread=False, because FastAPI serves requests from
        more than one thread;
      - a StaticPool for in-memory URLs, otherwise every new connection
        gets its own separate, empty database;
      - PRAGMA foreign_keys=ON on every connection, because SQLite
        silently IGNORES foreign keys unless asked.
    """
    url = url or settings.database_url
    kwargs: dict = {}

    is_sqlite = url.startswith("sqlite")
    if is_sqlite:
        kwargs["connect_args"] = {"check_same_thread": False}
        if url in _IN_MEMORY_SQLITE_URLS:
            kwargs["poolclass"] = StaticPool

    engine = create_engine(url, **kwargs)

    if is_sqlite:
        @event.listens_for(engine, "connect")
        def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker:
    # expire_on_commit=False so objects stay readable after commit -
    # callers convert them to domain dataclasses with to_domain().
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db(engine: Engine) -> None:
    """
    Creates any missing tables. Fine while the schema is still moving;
    it never alters an existing table, so once the schema stabilizes
    (Phase 3 close-out) real migrations (Alembic) take over.
    """
    Base.metadata.create_all(engine)


@contextmanager
def session_scope(session_factory: sessionmaker) -> Iterator[Session]:
    """Commit on success, roll back on any exception, always close."""
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
