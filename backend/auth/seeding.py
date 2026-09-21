# backend/auth/seeding.py

"""
Demo credentials (Day 76). Development only: gives every listed user the
same well-known password so the demo and frontend can log in. Users who
already have a password are skipped, so a password someone changed is
never overwritten on the next startup.
"""

from auth.credentials import CredentialStore
from auth.roles import ROLE_ADMIN
from auth.service import AuthService


def seed_demo_credentials(auth_service: AuthService, credentials: CredentialStore, user_ids: list[str], password: str) -> int:
    seeded = 0
    for user_id in user_ids:
        if credentials.get_password_hash(user_id) is None:
            auth_service.set_password(user_id, password)
            seeded += 1
    return seeded


def seed_demo_roles(auth_service: AuthService, admin_user_ids: list[str]) -> None:
    """Development only: makes the listed users API admins. Safe to repeat."""
    for user_id in admin_user_ids:
        auth_service.set_role(user_id, ROLE_ADMIN)
