# backend/database/session.py

"""
Engine and session plumbing (Day 71).

Nothing here runs at import time - no engine is created and no
database file is touched until make_engine() is called, so importing
this module (or running the test suite) never creates a stray .db file.
"""

from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import Engine, create_engine, event, inspect
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


class SchemaOutOfDateError(RuntimeError):
    """The database file predates the current models (a column or table is missing)."""


def check_schema(engine: Engine) -> None:
    """
    Fails loudly, with a plain instruction, if an existing database is
    missing tables or columns the models expect. create_all() never alters
    an existing table, so after a model gains a column an old development
    database would otherwise fail later with an obscure "no such column"
    error in the middle of a request. (Real migrations - Alembic - replace
    this once the schema stops moving.)
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    problems = []
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            problems.append(f"table '{table.name}' is missing")
            continue
        existing_columns = {column["name"] for column in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name not in existing_columns:
                problems.append(f"column '{table.name}.{column.name}' is missing")

    if problems:
        raise SchemaOutOfDateError(
            "The database schema is out of date: " + "; ".join(problems) + ". "
            "For a development database, delete the file (e.g. governai_dev.db) and it "
            "will be recreated and reseeded on the next start."
        )


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
