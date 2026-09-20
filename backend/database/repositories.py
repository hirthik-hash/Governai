# backend/database/repositories.py

"""
CRUD repositories over the persistence models (Day 74).

Each repository takes a Session and speaks in DOMAIN objects (User,
Resource, AuditRecord, LogEntry) - never ORM models - so callers stay
independent of SQLAlchemy. Repositories flush but never commit: the
caller owns the transaction (wrap work in session_scope()), exactly as
seed_database() does.

Rules enforced here, before the database is asked:
  - reports_to may never point at a missing user, at oneself, or close a
    loop (the escalation agent walks this chain; a cycle would make it
    guess). Seeding already refused cycles; this closes the same hole
    for updates.
  - a user or resource that other rows depend on cannot be deleted.
The database constraints remain as the backstop.

Deliberately NOT here yet: a policy repository. What ingestion stores is
decided in Phase 4 (Days 86-88).
"""

from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agents.audit_agent import AuditRecord
from core.decision_logger import LogEntry, LogEntryType
from data.seed_data import Resource, Sensitivity, User
from database.models import AuditRecordModel, DecisionLogEntryModel, ResourceModel, UserModel


class RepositoryError(Exception):
    pass


class RecordNotFoundError(RepositoryError, ValueError):
    """
    Also a ValueError on purpose: the seed lookups (get_user/get_resource)
    signal "not found" with ValueError, and the agents catch exactly that.
    A database-backed lookup can therefore replace them without any agent
    changing its error handling.
    """


class DuplicateRecordError(RepositoryError):
    pass


class RecordInUseError(RepositoryError):
    pass


class InvalidReportingChainError(RepositoryError):
    pass


class UserRepository:

    def __init__(self, session: Session):
        self._session = session

    def exists(self, user_id: str) -> bool:
        return self._session.get(UserModel, user_id) is not None

    def get(self, user_id: str) -> User:
        return self._get_model(user_id).to_domain()

    def list_all(self, department: Optional[str] = None) -> list[User]:
        statement = select(UserModel).order_by(UserModel.id)
        if department is not None:
            statement = statement.where(UserModel.department == department)
        return [m.to_domain() for m in self._session.scalars(statement)]

    def add(self, user: User) -> None:
        if self.exists(user.id):
            raise DuplicateRecordError(f"User {user.id} already exists")
        self._check_reporting_chain(user)
        self._session.add(UserModel.from_domain(user))
        self._session.flush()

    def update(self, user: User) -> None:
        model = self._get_model(user.id)
        self._check_reporting_chain(user)
        model.name = user.name
        model.department = user.department
        model.role = user.role
        model.clearance_level = user.clearance_level
        model.is_blacklisted = user.is_blacklisted
        model.reports_to = user.reports_to or None
        self._session.flush()

    def delete(self, user_id: str) -> None:
        model = self._get_model(user_id)
        subordinates = self._count(UserModel, UserModel.reports_to == user_id)
        as_requester = self._count(AuditRecordModel, AuditRecordModel.user_id == user_id)
        as_approver = self._count(AuditRecordModel, AuditRecordModel.approver_user_id == user_id)
        if subordinates or as_requester or as_approver:
            raise RecordInUseError(
                f"User {user_id} cannot be deleted: {subordinates} report to them and "
                f"{as_requester + as_approver} audit records reference them"
            )
        self._session.delete(model)
        self._session.flush()

    def _get_model(self, user_id: str) -> UserModel:
        model = self._session.get(UserModel, user_id)
        if model is None:
            raise RecordNotFoundError(f"No user with id {user_id}")
        return model

    def _count(self, model, condition) -> int:
        return self._session.scalar(select(func.count()).select_from(model).where(condition))

    def _check_reporting_chain(self, user: User) -> None:
        if not user.reports_to:
            return
        if user.reports_to == user.id:
            raise InvalidReportingChainError(f"User {user.id} cannot report to themselves")

        visited = set()
        current = user.reports_to
        while current:
            if current == user.id:
                raise InvalidReportingChainError(
                    f"Setting {user.id}.reports_to={user.reports_to} would create a reporting loop"
                )
            if current in visited:  # a loop that already exists elsewhere; stop walking
                break
            visited.add(current)
            approver = self._session.get(UserModel, current)
            if approver is None:
                raise RecordNotFoundError(f"reports_to references unknown user {current}")
            current = approver.reports_to


class ResourceRepository:

    def __init__(self, session: Session):
        self._session = session

    def exists(self, resource_id: str) -> bool:
        return self._session.get(ResourceModel, resource_id) is not None

    def get(self, resource_id: str) -> Resource:
        return self._get_model(resource_id).to_domain()

    def list_all(
        self,
        department: Optional[str] = None,
        sensitivity: Optional[Sensitivity] = None,
    ) -> list[Resource]:
        statement = select(ResourceModel).order_by(ResourceModel.id)
        if department is not None:
            statement = statement.where(ResourceModel.department == department)
        if sensitivity is not None:
            statement = statement.where(ResourceModel.sensitivity == sensitivity)
        return [m.to_domain() for m in self._session.scalars(statement)]

    def find_by_name(self, name_query: str) -> list[Resource]:
        """
        Case-insensitive partial match on the name, ordered by id -
        identical semantics to the seed-data find_resources_by_name().
        Filtering happens in Python, not SQL: SQL lower()/LIKE differ
        between SQLite and Postgres for non-ASCII text and treat % and _
        as wildcards, and this must never behave differently from the
        seed lookup. Fine for a resource table this size; revisit with a
        Postgres ILIKE query if it ever grows large.
        """
        needle = name_query.lower()
        return [r for r in self.list_all() if needle in r.name.lower()]

    def add(self, resource: Resource) -> None:
        if self.exists(resource.id):
            raise DuplicateRecordError(f"Resource {resource.id} already exists")
        self._session.add(ResourceModel.from_domain(resource))
        self._session.flush()

    def update(self, resource: Resource) -> None:
        model = self._get_model(resource.id)
        model.name = resource.name
        model.department = resource.department
        model.sensitivity = resource.sensitivity
        model.resource_type = resource.resource_type
        self._session.flush()

    def delete(self, resource_id: str) -> None:
        model = self._get_model(resource_id)
        referenced = self._session.scalar(
            select(func.count()).select_from(AuditRecordModel).where(AuditRecordModel.resource_id == resource_id)
        )
        if referenced:
            raise RecordInUseError(
                f"Resource {resource_id} cannot be deleted: {referenced} audit records reference it"
            )
        self._session.delete(model)
        self._session.flush()

    def _get_model(self, resource_id: str) -> ResourceModel:
        model = self._session.get(ResourceModel, resource_id)
        if model is None:
            raise RecordNotFoundError(f"No resource with id {resource_id}")
        return model


class AuditRecordRepository:
    """Append and read only - the ledger has no update or delete."""

    def __init__(self, session: Session):
        self._session = session

    def add(self, record: AuditRecord) -> None:
        self._session.add(AuditRecordModel.from_domain(record))
        self._session.flush()

    def all(self) -> list[AuditRecord]:
        statement = select(AuditRecordModel).order_by(AuditRecordModel.id)
        return [m.to_domain() for m in self._session.scalars(statement)]

    def for_request(self, request_id: str) -> list[AuditRecord]:
        statement = (
            select(AuditRecordModel)
            .where(AuditRecordModel.request_id == request_id)
            .order_by(AuditRecordModel.id)
        )
        return [m.to_domain() for m in self._session.scalars(statement)]

    def has_final_record(self, request_id: str) -> bool:
        """
        True once the request has a GRANTED or DENIED record. The pipeline
        must ask this BEFORE deciding (Days 78-79): the database's unique
        index would reject a replay too, but only after the decision was made.
        """
        statement = (
            select(func.count())
            .select_from(AuditRecordModel)
            .where(
                AuditRecordModel.request_id == request_id,
                AuditRecordModel.final_decision.in_(("GRANTED", "DENIED")),
            )
        )
        return self._session.scalar(statement) > 0


class DecisionLogRepository:
    """Append and read only, like the DecisionLogger it persists."""

    def __init__(self, session: Session):
        self._session = session

    def add(self, entry: LogEntry) -> None:
        self._session.add(DecisionLogEntryModel.from_domain(entry))
        self._session.flush()

    def add_many(self, entries: list[LogEntry]) -> None:
        self._session.add_all(DecisionLogEntryModel.from_domain(e) for e in entries)
        self._session.flush()

    def all(self) -> list[LogEntry]:
        return self._entries()

    def for_request(self, request_id: str) -> list[LogEntry]:
        return self._entries(DecisionLogEntryModel.request_id == request_id)

    def by_type(self, entry_type: LogEntryType) -> list[LogEntry]:
        return self._entries(DecisionLogEntryModel.entry_type == entry_type)

    def _entries(self, condition=None) -> list[LogEntry]:
        statement = select(DecisionLogEntryModel).order_by(DecisionLogEntryModel.id)
        if condition is not None:
            statement = statement.where(condition)
        return [m.to_domain() for m in self._session.scalars(statement)]
