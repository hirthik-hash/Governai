# backend/database/directory.py

"""
Database-backed Directory (Day 75).

Satisfies the same contract as data.directory.SeedDirectory, so the
request and escalation agents run unchanged against the database. Each
lookup opens its own short-lived session and closes it: sessions are not
thread-safe, and FastAPI serves requests from several threads, so one
long-lived shared session would be a bug waiting to happen. The
repositories' RecordNotFoundError is a ValueError, which is exactly what
the agents already catch.
"""

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy.orm import Session, sessionmaker

from data.seed_data import Resource, User
from database.repositories import ResourceRepository, UserRepository


class DatabaseDirectory:

    def __init__(self, session_factory: sessionmaker):
        self._session_factory = session_factory

    @contextmanager
    def _read_session(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
        finally:
            session.close()

    def get_user(self, user_id: str) -> User:
        with self._read_session() as session:
            return UserRepository(session).get(user_id)

    def get_resource(self, resource_id: str) -> Resource:
        with self._read_session() as session:
            return ResourceRepository(session).get(resource_id)

    def find_resources_by_name(self, name_query: str) -> list[Resource]:
        with self._read_session() as session:
            return ResourceRepository(session).find_by_name(name_query)
