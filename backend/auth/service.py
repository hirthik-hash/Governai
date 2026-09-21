# backend/auth/service.py

"""
AuthService (Day 76): password login and token authentication.

login() always fails the same way (AuthenticationError) whether the user
does not exist, has no password, or gave the wrong one, and it spends
comparable time in each case. The specific reason goes to the server log
only.
"""

from data.directory import Directory
from data.seed_data import User
from auth.credentials import CredentialStore
from auth.errors import AuthenticationError
from auth.passwords import PasswordService
from auth.tokens import IssuedToken, TokenService
from core.app_logger import get_logger

logger = get_logger("auth")


class AuthService:

    def __init__(
        self,
        credentials: CredentialStore,
        directory: Directory,
        passwords: PasswordService,
        tokens: TokenService,
    ):
        self._credentials = credentials
        self._directory = directory
        self._passwords = passwords
        self._tokens = tokens

    def login(self, user_id: str, password: str) -> IssuedToken:
        stored_hash = self._credentials.get_password_hash(user_id) if user_id else None

        if stored_hash is None:
            self._passwords.verify_against_dummy(password)
            self._fail(user_id, "no such user or no password set")

        if not self._passwords.verify(stored_hash, password):
            self._fail(user_id, "wrong password")

        try:
            self._directory.get_user(user_id)
        except ValueError:
            self._fail(user_id, "user no longer exists in the directory")

        if self._passwords.needs_rehash(stored_hash):
            # The hashing costs were raised since this password was stored.
            self._credentials.set_password_hash(user_id, self._passwords.hash(password, enforce_policy=False))

        return self._tokens.issue(user_id)

    def authenticate_token(self, token: str) -> User:
        """The user a valid token belongs to, looked up fresh. Raises AuthenticationError."""
        user_id = self._tokens.verify(token)
        try:
            return self._directory.get_user(user_id)
        except ValueError:
            logger.warning("token for a user that no longer exists: %r", user_id)
            raise AuthenticationError("token subject no longer exists")

    def set_password(self, user_id: str, new_password: str) -> None:
        """Validates the policy, then stores a fresh hash. ValueError if the user is unknown."""
        self._directory.get_user(user_id)  # raises ValueError for an unknown user
        self._credentials.set_password_hash(user_id, self._passwords.hash(new_password))

    def _fail(self, user_id: str, reason: str) -> None:
        logger.warning("login failed for %r: %s", user_id, reason)
        raise AuthenticationError("Invalid credentials")
