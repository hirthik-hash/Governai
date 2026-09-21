# backend/database/credentials.py

"""Database-backed CredentialStore (Day 76): password hashes on the users table."""

from typing import Optional

from sqlalchemy.orm import sessionmaker

from database.models import UserModel
from database.repositories import RecordNotFoundError
from database.session import session_scope


class DatabaseCredentialStore:

    def __init__(self, session_factory: sessionmaker):
        self._session_factory = session_factory

    def get_password_hash(self, user_id: str) -> Optional[str]:
        session = self._session_factory()
        try:
            user = session.get(UserModel, user_id)
            return user.password_hash if user is not None else None
        finally:
            session.close()

    def set_password_hash(self, user_id: str, password_hash: str) -> None:
        with session_scope(self._session_factory) as session:
            user = session.get(UserModel, user_id)
            if user is None:
                raise RecordNotFoundError(f"No user with id {user_id}")
            user.password_hash = password_hash
