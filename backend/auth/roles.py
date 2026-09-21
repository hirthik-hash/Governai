# backend/auth/roles.py

"""
API roles (Day 77). Two, deliberately:

  user  - authenticated; can submit requests as themselves, see and
          resolve the escalations assigned to them.
  admin - additionally: the audit ledger, all pending escalations, the
          health endpoints.

These are permissions on the HTTP API, not governance concepts: they are
unrelated to a user's job title (User.role) or clearance, which the FSM
and agents use to decide access to RESOURCES. An admin gets no extra
resource access, cannot submit on behalf of anyone, and cannot approve
an escalation that was routed to someone else.

Least privilege by default: anyone without an explicit assignment is a
"user".
"""

from typing import Protocol

ROLE_USER = "user"
ROLE_ADMIN = "admin"
VALID_ROLES = (ROLE_USER, ROLE_ADMIN)


class RoleStore(Protocol):

    def get_role(self, user_id: str) -> str: ...

    def set_role(self, user_id: str, role: str) -> None: ...


def validate_role(role: str) -> None:
    if role not in VALID_ROLES:
        raise ValueError(f"API role must be one of {VALID_ROLES}, got '{role}'")


class InMemoryRoleStore:

    def __init__(self):
        self._roles: dict[str, str] = {}

    def get_role(self, user_id: str) -> str:
        return self._roles.get(user_id, ROLE_USER)

    def set_role(self, user_id: str, role: str) -> None:
        validate_role(role)
        self._roles[user_id] = role
