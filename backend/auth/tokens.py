# backend/auth/tokens.py

"""
JWT issuing and verification (Day 76), HS256 by default.

Deliberate choices:
  - The verifier passes an explicit algorithm allow-list to the library
    and never trusts the token's own header, which closes the classic
    alg=none and algorithm-confusion attacks.
  - exp, iat, sub and iss are all REQUIRED; issuer must match.
  - The token carries only the subject (user id). Everything else -
    role, blacklist status, whether the user still exists - is looked up
    fresh on every request, so changes take effect immediately instead
    of when a token expires.
  - Expiry is checked against an injected clock (project convention),
    not the library's own real-time check, so tests never sleep.
  - Symmetric algorithms only, and a secret of at least 32 characters.
  - Access tokens only: there is no refresh token or revocation list
    yet. A stolen token is valid until it expires (a known gap).
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

import jwt

from auth.errors import TokenExpiredError, TokenInvalidError

MIN_SECRET_LENGTH = 32
ALLOWED_ALGORITHMS = ("HS256", "HS384", "HS512")
_REQUIRED_CLAIMS = ["exp", "iat", "sub", "iss"]


@dataclass(frozen=True)
class IssuedToken:
    access_token: str
    expires_in_seconds: int


class TokenService:

    def __init__(
        self,
        secret_key: str,
        algorithm: str = "HS256",
        expiry_minutes: int = 60,
        issuer: str = "governai",
        now_fn: Optional[Callable[[], datetime]] = None,
    ):
        if len(secret_key) < MIN_SECRET_LENGTH:
            raise ValueError(f"JWT secret key must be at least {MIN_SECRET_LENGTH} characters")
        if algorithm not in ALLOWED_ALGORITHMS:
            raise ValueError(f"JWT algorithm must be one of {ALLOWED_ALGORITHMS}, got '{algorithm}'")
        if expiry_minutes <= 0:
            raise ValueError("JWT expiry must be positive")

        self._secret_key = secret_key
        self._algorithm = algorithm
        self._expiry_seconds = expiry_minutes * 60
        self._issuer = issuer
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    def issue(self, user_id: str) -> IssuedToken:
        issued_at = int(self._now_fn().timestamp())
        payload = {
            "sub": user_id,
            "iss": self._issuer,
            "iat": issued_at,
            "exp": issued_at + self._expiry_seconds,
        }
        token = jwt.encode(payload, self._secret_key, algorithm=self._algorithm)
        return IssuedToken(access_token=token, expires_in_seconds=self._expiry_seconds)

    def verify(self, token: str) -> str:
        """Returns the user id, or raises TokenInvalidError / TokenExpiredError."""
        try:
            payload = jwt.decode(
                token,
                self._secret_key,
                algorithms=[self._algorithm],
                issuer=self._issuer,
                options={"require": _REQUIRED_CLAIMS, "verify_exp": False, "verify_iat": False},
            )
        except jwt.InvalidTokenError as error:
            raise TokenInvalidError(f"{type(error).__name__}: {error}") from error

        subject = payload.get("sub")
        expires_at = payload.get("exp")
        if not isinstance(subject, str) or not subject or not isinstance(expires_at, int):
            raise TokenInvalidError("sub must be a non-empty string and exp an integer")

        if int(self._now_fn().timestamp()) >= expires_at:
            raise TokenExpiredError("token has expired")

        return subject
