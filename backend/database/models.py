# backend/database/models.py

"""
SQLAlchemy persistence models (Day 71: users and resources).

These are deliberately SEPARATE from the domain dataclasses in
data/seed_data.py. The FSMs and agents keep working with plain
User/Resource dataclasses and never import SQLAlchemy; each model here
converts to and from its dataclass via to_domain()/from_domain(). That
keeps the deterministic core independent of the storage engine.

Day 72 adds audit records; Day 73 adds the decision log and policies.
"""

from typing import Optional

from sqlalchemy import (
    JSON, Boolean, CheckConstraint, Enum as SAEnum, ForeignKey, Index, Integer, String, event, text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from agents.audit_agent import AuditRecord
from data.seed_data import Resource, Sensitivity, User

MIN_CLEARANCE = 0
MAX_CLEARANCE = 5


class Base(DeclarativeBase):
    pass


class UserModel(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            f"clearance_level BETWEEN {MIN_CLEARANCE} AND {MAX_CLEARANCE}",
            name="ck_users_clearance_range",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    department: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    clearance_level: Mapped[int] = mapped_column(Integer, nullable=False)
    is_blacklisted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # NULL = top of the approval chain (the domain dataclass uses "").
    reports_to: Mapped[Optional[str]] = mapped_column(String, ForeignKey("users.id"), nullable=True)

    def to_domain(self) -> User:
        return User(
            id=self.id,
            name=self.name,
            department=self.department,
            role=self.role,
            clearance_level=self.clearance_level,
            is_blacklisted=self.is_blacklisted,
            reports_to=self.reports_to or "",
        )

    @classmethod
    def from_domain(cls, user: User) -> "UserModel":
        return cls(
            id=user.id,
            name=user.name,
            department=user.department,
            role=user.role,
            clearance_level=user.clearance_level,
            is_blacklisted=user.is_blacklisted,
            reports_to=user.reports_to or None,
        )


class ResourceModel(Base):
    """
    required_clearance is intentionally NOT a column: it is derived
    from sensitivity (SENSITIVITY_TO_REQUIRED_CLEARANCE), so storing
    it would create a second copy that could drift out of sync.
    """
    __tablename__ = "resources"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    department: Mapped[str] = mapped_column(String, nullable=False)
    # Stored as the enum's VALUE ("top_secret") with a CHECK constraint,
    # so the database itself rejects an unknown sensitivity.
    sensitivity: Mapped[Sensitivity] = mapped_column(
        SAEnum(
            Sensitivity,
            name="sensitivity",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    resource_type: Mapped[str] = mapped_column(String, nullable=False)

    def to_domain(self) -> Resource:
        return Resource(
            id=self.id,
            name=self.name,
            department=self.department,
            sensitivity=self.sensitivity,
            resource_type=self.resource_type,
        )

    @classmethod
    def from_domain(cls, resource: Resource) -> "ResourceModel":
        return cls(
            id=resource.id,
            name=resource.name,
            department=resource.department,
            sensitivity=resource.sensitivity,
            resource_type=resource.resource_type,
        )


class AuditRecordImmutableError(Exception):
    """Raised when code tries to UPDATE or DELETE a stored audit record."""


_FINAL_DECISION_SQL = "final_decision IN ('GRANTED', 'DENIED')"


class AuditRecordModel(Base):
    """
    Append-only compliance ledger (Day 72).

    request_id is deliberately NOT unique on its own: a request that
    safe mode blocks gets a PENDING record for each blocked attempt and
    a final record once it is decided. What the ledger guarantees
    instead is exactly ONE FINAL record (GRANTED or DENIED) per
    request_id - enforced by a partial unique index, so a request can
    never be recorded as both granted and denied, or granted twice.
    """
    __tablename__ = "audit_records"
    __table_args__ = (
        CheckConstraint(
            "final_decision IN ('GRANTED', 'DENIED', 'PENDING')",
            name="ck_audit_records_final_decision",
        ),
        CheckConstraint(
            "risk_score BETWEEN 0 AND 100",
            name="ck_audit_records_risk_score_range",
        ),
        Index(
            "uq_audit_records_one_final_per_request",
            "request_id",
            unique=True,
            sqlite_where=text(_FINAL_DECISION_SQL),
            postgresql_where=text(_FINAL_DECISION_SQL),
        ),
        Index("ix_audit_records_request_id", "request_id"),
    )

    # Surrogate key: request_id repeats (see above).
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String, nullable=False)
    # ISO 8601 string, exactly as the audit agent produces it. Its fixed
    # width makes lexical comparison correct, which is what
    # filter_records() already relies on.
    timestamp: Mapped[str] = mapped_column(String, nullable=False)
    # Foreign keys with no ON DELETE action: a user or resource that
    # appears in the ledger cannot be deleted out from under it.
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    resource_id: Mapped[str] = mapped_column(String, ForeignKey("resources.id"), nullable=False)
    action_requested: Mapped[str] = mapped_column(String, nullable=False)
    final_fsm_state: Mapped[str] = mapped_column(String, nullable=False)
    risk_score: Mapped[int] = mapped_column(Integer, nullable=False)
    risk_level: Mapped[str] = mapped_column(String, nullable=False)
    final_decision: Mapped[str] = mapped_column(String, nullable=False)
    # NULL = no approver (the dataclass uses "").
    approver_user_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    # Ordered list of strings; a JSON column keeps the order and works
    # on both SQLite and Postgres. Nothing queries inside it.
    agent_reasoning_trail: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    policy_rule_cited: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    def to_domain(self) -> AuditRecord:
        return AuditRecord(
            request_id=self.request_id,
            timestamp=self.timestamp,
            user_id=self.user_id,
            resource_id=self.resource_id,
            action_requested=self.action_requested,
            final_fsm_state=self.final_fsm_state,
            risk_score=self.risk_score,
            risk_level=self.risk_level,
            final_decision=self.final_decision,
            approver_user_id=self.approver_user_id or "",
            agent_reasoning_trail=list(self.agent_reasoning_trail),
            policy_rule_cited=self.policy_rule_cited,
        )

    @classmethod
    def from_domain(cls, record: AuditRecord) -> "AuditRecordModel":
        return cls(
            request_id=record.request_id,
            timestamp=record.timestamp,
            user_id=record.user_id,
            resource_id=record.resource_id,
            action_requested=record.action_requested,
            final_fsm_state=record.final_fsm_state,
            risk_score=record.risk_score,
            risk_level=record.risk_level,
            final_decision=record.final_decision,
            approver_user_id=record.approver_user_id or None,
            agent_reasoning_trail=list(record.agent_reasoning_trail),
            policy_rule_cited=record.policy_rule_cited,
        )


# Append-only guard. This stops the ORM (Session.add/flush/delete) from
# changing a stored record. It does NOT stop raw SQL or a bulk
# UPDATE/DELETE statement - real enforcement is database privileges
# (an INSERT-only role for the application), a Day 172 security-review item.
@event.listens_for(AuditRecordModel, "before_update")
def _audit_records_cannot_be_updated(_mapper, _connection, target):
    raise AuditRecordImmutableError(
        f"Audit record {target.id} (request {target.request_id}) is append-only and cannot be updated"
    )


@event.listens_for(AuditRecordModel, "before_delete")
def _audit_records_cannot_be_deleted(_mapper, _connection, target):
    raise AuditRecordImmutableError(
        f"Audit record {target.id} (request {target.request_id}) is append-only and cannot be deleted"
    )
