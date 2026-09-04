# backend/fsm/states.py

from enum import Enum


class RequestState(Enum):
    """
    States for a single access request as it flows through the
    governance pipeline, from first contact to final resolution.
    """

    # Entry
    IDLE = "idle"
    REQUEST_RECEIVED = "request_received"

    # Parsing
    PARSING_REQUEST = "parsing_request"
    CLARIFICATION_REQUESTED = "clarification_requested"

    # Validation
    VALIDATING_ACCESS = "validating_access"

    # Outcomes of validation
    AUTHORIZED = "authorized"
    ESCALATION_REQUIRED = "escalation_required"
    HARD_DENIED = "hard_denied"

    # Escalation flow
    MANAGER_REVIEW = "manager_review"

    # Final resolution states
    ACCESS_GRANTED = "access_granted"
    DENIED_FINAL = "denied_final"

    # Post-decision
    AUDIT_LOGGING = "audit_logging"
    CLOSED = "closed"


class SystemState(Enum):
    """
    States for overall system health, independent of any single
    request. This is the parallel 'Safe Mode' FSM described in the
    roadmap — it governs graceful degradation, not access decisions.
    """

    SYSTEM_NORMAL = "system_normal"
    DEGRADED_WARNING = "degraded_warning"
    SAFE_MODE_ACTIVE = "safe_mode_active"
    RESTORING = "restoring"


# Terminal states — once a request reaches one of these, it cannot
# transition further. Useful later for validation and for the
# audit logger to know when a request's lifecycle is complete.
TERMINAL_REQUEST_STATES = {
    RequestState.CLOSED,
    RequestState.DENIED_FINAL,
}