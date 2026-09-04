# backend/data/seed_data.py

from dataclasses import dataclass, field
from enum import Enum


class Sensitivity(Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    RESTRICTED = "restricted"
    TOP_SECRET = "top_secret"


# Maps sensitivity -> the minimum clearance level required to access it.
# Used by Phase 2's Access Validation Agent.
SENSITIVITY_TO_REQUIRED_CLEARANCE = {
    Sensitivity.PUBLIC: 0,
    Sensitivity.INTERNAL: 1,
    Sensitivity.RESTRICTED: 3,
    Sensitivity.TOP_SECRET: 5,
}


@dataclass
class User:
    id: str
    name: str
    department: str
    role: str
    clearance_level: int  # 0-5
    is_blacklisted: bool = False


@dataclass
class Resource:
    id: str
    name: str
    department: str
    sensitivity: Sensitivity
    resource_type: str  # e.g. "document", "database", "system"

    @property
    def required_clearance(self) -> int:
        return SENSITIVITY_TO_REQUIRED_CLEARANCE[self.sensitivity]


# --- Seed users: at least one per clearance level 0-5, across
# multiple departments, plus one deliberately blacklisted user. ---

SEED_USERS: list[User] = [
    User(id="user-001", name="Alex Chen", department="engineering", role="Software Engineer", clearance_level=1),
    User(id="user-002", name="Priya Nair", department="engineering", role="Team Lead", clearance_level=2),
    User(id="user-003", name="Marcus Webb", department="finance", role="Financial Analyst", clearance_level=1),
    User(id="user-004", name="Sofia Ricci", department="finance", role="Finance Manager", clearance_level=3),
    User(id="user-005", name="David Okafor", department="hr", role="HR Coordinator", clearance_level=1),
    User(id="user-006", name="Elena Petrova", department="hr", role="HR Director", clearance_level=4),
    User(id="user-007", name="James Whitfield", department="security", role="CISO", clearance_level=5),
    User(id="user-008", name="Rina Sato", department="engineering", role="Junior Developer", clearance_level=0),
    User(id="user-009", name="Tom Bracken", department="sales", role="Sales Rep", clearance_level=1,
         is_blacklisted=True),  # flagged for a prior policy violation
    User(id="user-010", name="Layla Hassan", department="finance", role="Director", clearance_level=4),
]


# --- Seed resources: at least one per sensitivity level, across
# multiple departments and resource types. ---

SEED_RESOURCES: list[Resource] = [
    Resource(id="resource-001", name="Employee Handbook", department="hr",
             sensitivity=Sensitivity.PUBLIC, resource_type="document"),
    Resource(id="resource-002", name="Team Sprint Board", department="engineering",
             sensitivity=Sensitivity.INTERNAL, resource_type="system"),
    Resource(id="resource-003", name="Q4 Financial Report", department="finance",
             sensitivity=Sensitivity.RESTRICTED, resource_type="document"),
    Resource(id="resource-004", name="Production Database", department="engineering",
             sensitivity=Sensitivity.RESTRICTED, resource_type="database"),
    Resource(id="resource-005", name="Employee Salary Records", department="hr",
             sensitivity=Sensitivity.TOP_SECRET, resource_type="database"),
    Resource(id="resource-006", name="Merger Negotiation Documents", department="finance",
             sensitivity=Sensitivity.TOP_SECRET, resource_type="document"),
    Resource(id="resource-007", name="Company Blog Drafts", department="sales",
             sensitivity=Sensitivity.PUBLIC, resource_type="document"),
    Resource(id="resource-008", name="Security Incident Log", department="security",
             sensitivity=Sensitivity.RESTRICTED, resource_type="system"),
]


def get_user(user_id: str) -> User:
    for user in SEED_USERS:
        if user.id == user_id:
            return user
    raise ValueError(f"No seed user with id {user_id}")


def get_resource(resource_id: str) -> Resource:
    for resource in SEED_RESOURCES:
        if resource.id == resource_id:
            return resource
    raise ValueError(f"No seed resource with id {resource_id}")


def build_fsm_context(user: User, resource: Resource, risk_score: int = 10, **overrides) -> dict:
    """
    Converts a (User, Resource) pair from the seed dataset into a
    context dict compatible with GovernanceFSM.transition(). This is
    the bridge between "realistic fake data" and the FSM we built in
    Phase 1 - agents in Phase 2 will build contexts this same way,
    just with computed values instead of a fixed risk_score.
    """
    context = {
        "ambiguity_flag": False,
        "clearance": user.clearance_level,
        "required_clearance": resource.required_clearance,
        "risk_score": risk_score,
        "blacklist_match": user.is_blacklisted,
    }
    context.update(overrides)
    return context