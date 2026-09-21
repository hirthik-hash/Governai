# backend/auth/seeding.py

"""
Demo credentials (Day 76). Development only: gives every listed user the
same well-known password so the demo and frontend can log in. Users who
already have a password are skipped, so a password someone changed is
never overwritten on the next startup.
"""

from auth.credentials import CredentialStore
from auth.service import AuthService


def seed_demo_credentials(auth_service: AuthService, credentials: CredentialStore, user_ids: list[str], password: str) -> int:
    seeded = 0
    for user_id in user_ids:
        if credentials.get_password_hash(user_id) is None:
            auth_service.set_password(user_id, password)
            seeded += 1
    return seeded
