# backend/auth/credentials.py

"""
Where password hashes live (Day 76). Credentials are deliberately NOT
part of the domain User: agents and the FSM never see them. The
CredentialStore protocol keeps AuthService independent of storage; the
database implementation is database/credentials.py.
"""

from typing import Optional, Protocol


class CredentialStore(Protocol):

    def get_password_hash(self, user_id: str) -> Optional[str]: ...

    def set_password_hash(self, user_id: str, password_hash: str) -> None: ...


class InMemoryCredentialStore:
    """Empty by default, so an app built without real credentials lets nobody log in."""

    def __init__(self):
        self._hashes: dict[str, str] = {}

    def get_password_hash(self, user_id: str) -> Optional[str]:
        return self._hashes.get(user_id)

    def set_password_hash(self, user_id: str, password_hash: str) -> None:
        self._hashes[user_id] = password_hash
