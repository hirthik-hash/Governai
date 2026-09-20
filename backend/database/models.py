# backend/database/models.py

"""
SQLAlchemy persistence models (Day 71: users and resources).

These are deliberately SEPARATE from the domain dataclasses in
data/seed_data.py. The FSMs and agents keep working with plain
User/Resource dataclasses and never import SQLAlchemy; each model here
converts to and from its dataclass via to_domain()/from_domain(). That
keeps the deterministic core independent of the storage engine.

Days 72-73 add policies and logs to this same module.
"""

from typing import Optional

from sqlalchemy import Boolean, CheckConstraint, Enum as SAEnum, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

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
