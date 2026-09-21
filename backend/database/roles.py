# backend/database/roles.py

"""Database-backed RoleStore (Day 77): users.api_role."""

from sqlalchemy.orm import sessionmaker

from auth.roles import ROLE_USER, validate_role
from database.models import UserModel
from database.repositories import RecordNotFoundError
from database.session import session_scope


class DatabaseRoleStore:

    def __init__(self, session_factory: sessionmaker):
        self._session_factory = session_factory

    def get_role(self, user_id: str) -> str:
        session = self._session_factory()
        try:
            user = session.get(UserModel, user_id)
            return user.api_role if user is not None else ROLE_USER
        finally:
            session.close()

    def set_role(self, user_id: str, role: str) -> None:
        validate_role(role)
        with session_scope(self._session_factory) as session:
            user = session.get(UserModel, user_id)
            if user is None:
                raise RecordNotFoundError(f"No user with id {user_id}")
            user.api_role = role
