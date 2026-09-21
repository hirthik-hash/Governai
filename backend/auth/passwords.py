# backend/auth/passwords.py

"""
Password hashing (Day 76): Argon2id via the argon2-cffi library.

Never hand-rolled: salting, the encoded hash format and the cost
parameters all come from the library's vetted defaults. Cost parameters
can be lowered for tests only by passing them in explicitly.

Policy: 12-128 characters, enforced when a password is SET. Login never
applies the policy (a legacy weak password must still be able to log in
so its owner can change it); it only caps length to keep an attacker
from making the server hash megabytes of input.
"""

from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from auth.errors import WeakPasswordError

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128
MAX_LOGIN_ATTEMPT_LENGTH = 1024


class PasswordService:

    def __init__(
        self,
        time_cost: Optional[int] = None,
        memory_cost: Optional[int] = None,
        parallelism: Optional[int] = None,
    ):
        costs = {
            name: value
            for name, value in (("time_cost", time_cost), ("memory_cost", memory_cost), ("parallelism", parallelism))
            if value is not None
        }
        self._hasher = PasswordHasher(**costs)
        # A real hash of a throwaway value, made with the same costs, so
        # verifying against it takes as long as a genuine verification.
        self._dummy_hash = self._hasher.hash("throwaway-value-used-only-for-timing")

    def validate_strength(self, password: str) -> None:
        if len(password) < MIN_PASSWORD_LENGTH:
            raise WeakPasswordError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
        if len(password) > MAX_PASSWORD_LENGTH:
            raise WeakPasswordError(f"Password must be at most {MAX_PASSWORD_LENGTH} characters")

    def hash(self, password: str, enforce_policy: bool = True) -> str:
        if enforce_policy:
            self.validate_strength(password)
        return self._hasher.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        if len(password) > MAX_LOGIN_ATTEMPT_LENGTH:
            return False
        try:
            return self._hasher.verify(password_hash, password)
        except (VerificationError, InvalidHashError):
            return False

    def needs_rehash(self, password_hash: str) -> bool:
        return self._hasher.check_needs_rehash(password_hash)

    def verify_against_dummy(self, password: str) -> None:
        """
        Spend the same time a real verification would. Called when the
        user has no stored hash, so login timing does not reveal which
        user ids exist.
        """
        self.verify(self._dummy_hash, password)
