# backend/database/seeding.py

"""
Loads the seed dataset (data/seed_data.py) into the database (Day 71).

Insert-only and idempotent: rows whose id already exists are skipped,
never overwritten, so re-running at every startup cannot clobber edits
made later. The caller owns the transaction - this function flushes
but does not commit (wrap it in session_scope()).
"""

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from data.seed_data import SEED_RESOURCES, SEED_USERS, Resource, User
from database.models import ResourceModel, UserModel


@dataclass
class SeedResult:
    users_inserted: int
    resources_inserted: int


def _insert_users_in_chain_order(session: Session, users: list[User]) -> int:
    """
    users.reports_to is a foreign key, so an approver must exist before
    anyone who reports to them. Inserts in layers (top of the chain
    first). Raises ValueError - inserting nothing further - if a user
    reports to someone who does not exist or the chain contains a cycle.
    """
    placed = set(session.scalars(select(UserModel.id)))
    remaining = [u for u in users if u.id not in placed]
    inserted = 0

    while remaining:
        layer = [u for u in remaining if not u.reports_to or u.reports_to in placed]
        if not layer:
            stuck = ", ".join(sorted(f"{u.id}->{u.reports_to}" for u in remaining))
            raise ValueError(f"reports_to references an unknown user or forms a cycle: {stuck}")

        session.add_all(UserModel.from_domain(u) for u in layer)
        session.flush()
        inserted += len(layer)
        placed.update(u.id for u in layer)
        layer_ids = {u.id for u in layer}
        remaining = [u for u in remaining if u.id not in layer_ids]

    return inserted


def seed_database(
    session: Session,
    users: Optional[list[User]] = None,
    resources: Optional[list[Resource]] = None,
) -> SeedResult:
    users = SEED_USERS if users is None else users
    resources = SEED_RESOURCES if resources is None else resources

    users_inserted = _insert_users_in_chain_order(session, users)

    existing_resource_ids = set(session.scalars(select(ResourceModel.id)))
    new_resources = [r for r in resources if r.id not in existing_resource_ids]
    session.add_all(ResourceModel.from_domain(r) for r in new_resources)
    session.flush()

    return SeedResult(users_inserted=users_inserted, resources_inserted=len(new_resources))
