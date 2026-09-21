# backend/data/directory.py

"""
The Directory: how agents look up users and resources (Day 75).

Until now the request and escalation agents called seed-data functions
directly, which tied them to in-memory seed lists. They now receive a
Directory instead, so the same agent code can be backed by the seed lists
(SeedDirectory, the default - what every existing test uses) or by the
database (database.directory.DatabaseDirectory).

Contract every Directory must honor, because the agents depend on it:
  - get_user / get_resource raise ValueError when the id is unknown;
  - find_resources_by_name is a case-insensitive partial match on the
    name, returned in id order (zero, one or many results - the many
    case is the ambiguity the request agent reports rather than guesses).
"""

from typing import Optional, Protocol, runtime_checkable

from data.seed_data import SEED_RESOURCES, SEED_USERS, Resource, User


@runtime_checkable
class Directory(Protocol):

    def get_user(self, user_id: str) -> User: ...

    def get_resource(self, resource_id: str) -> Resource: ...

    def find_resources_by_name(self, name_query: str) -> list[Resource]: ...


class SeedDirectory:
    """
    In-memory Directory. With no arguments it reads the live SEED_USERS /
    SEED_RESOURCES lists; pass your own lists (e.g. the demo dataset) to
    use those instead.
    """

    def __init__(
        self,
        users: Optional[list[User]] = None,
        resources: Optional[list[Resource]] = None,
    ):
        self._users = SEED_USERS if users is None else users
        self._resources = SEED_RESOURCES if resources is None else resources

    def get_user(self, user_id: str) -> User:
        for user in self._users:
            if user.id == user_id:
                return user
        raise ValueError(f"No user with id {user_id}")

    def get_resource(self, resource_id: str) -> Resource:
        for resource in self._resources:
            if resource.id == resource_id:
                return resource
        raise ValueError(f"No resource with id {resource_id}")

    def find_resources_by_name(self, name_query: str) -> list[Resource]:
        query_lower = name_query.lower()
        return [r for r in self._resources if query_lower in r.name.lower()]
