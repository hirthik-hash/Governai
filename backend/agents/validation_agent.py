# backend/agents/validation_agent.py

"""
Access Validation Agent (GovernAI Agent 2 of 7).

Takes RequestUnderstandingAgent's output (not raw IDs) and adds
explicit, explainable permission reasoning on top of the raw
clearance numbers the FSM already compares natively. This agent's
value is NOT recomputing clearance >= required_clearance - the FSM
already does that arithmetic correctly. Its value is:

  1. Producing a human-readable explanation of the RBAC decision,
     for later use by the Explainability Center.
  2. Applying role-based overrides that a simple numeric clearance
     threshold cannot express (e.g. a CISO's role grants scope over
     security-classified resources regardless of raw clearance).
  3. Flagging after-hours access to sensitive resources.
  4. Checking session/token validity through a swappable validator.

This agent does not itself grant or deny access - it enriches the
context with validation results and reasoning that later
agents/humans can inspect, while the FSM remains the sole authority
on the actual decision.
"""

from datetime import datetime, time
from typing import Callable

from agents.base_agent import BaseAgent, AgentResult


ROLE_OVERRIDES = {
    "CISO": {"security", "top_secret"},
}

# Simplified business hours: Mon-Fri, 8:00-18:00. No timezone or
# holiday handling - a deliberate simplification for this project,
# not an oversight. Document any change to this here.
BUSINESS_HOURS_START = time(8, 0)
BUSINESS_HOURS_END = time(18, 0)
BUSINESS_DAYS = {0, 1, 2, 3, 4}  # Monday=0 ... Sunday=6

# Only these sensitivities trigger an after-hours flag - lower
# sensitivities aren't worth flagging, consistent with proportionate
# response elsewhere in the system.
AFTER_HOURS_SENSITIVE_LEVELS = {"restricted", "top_secret"}


def is_business_hours(dt: datetime) -> bool:
    return (
        dt.weekday() in BUSINESS_DAYS
        and BUSINESS_HOURS_START <= dt.time() <= BUSINESS_HOURS_END
    )


class SessionValidator:
    """
    Minimal, swappable session/token validity check. This default
    implementation only checks for a non-empty session_token and an
    explicit session_expired flag - no real JWT verification, since
    that infrastructure doesn't exist until Phase 3 (Days 76-77).

    Phase 3 should subclass or replace this with one that validates
    real JWTs against core.config.settings.jwt_secret_key, without
    requiring any change to AccessValidationAgent's own code - only
    to whatever constructs it.
    """

    def is_valid(self, input_data: dict) -> tuple[bool, str]:
        """Returns (is_valid, reason)."""
        session_token = input_data.get("session_token")
        session_expired = input_data.get("session_expired", False)

        if not session_token:
            return False, "No session token provided"

        if session_expired:
            return False, "Session token has expired"

        return True, "Session valid"


class AccessValidationAgent(BaseAgent):
    """
    Permission gatekeeper: takes the output of RequestUnderstandingAgent
    and produces an explicit RBAC explanation, applies role-based
    overrides, flags after-hours access to sensitive resources, and
    checks session validity. Does not itself deny access - the FSM
    remains the actual decision-maker.
    """

    def __init__(
        self,
        now_fn: Callable[[], datetime] = None,
        session_validator: SessionValidator = None,
    ):
        super().__init__()
        self._now_fn = now_fn or datetime.now
        self._session_validator = (
            session_validator or SessionValidator()
        )

    @property
    def agent_name(self) -> str:
        return "access_validation"

    def process(self, input_data: dict) -> AgentResult:
        clearance = input_data.get("clearance")
        required_clearance = input_data.get("required_clearance")
        role = input_data.get("role")
        resource_sensitivity = input_data.get("resource_sensitivity")

        if clearance is None or required_clearance is None:
            return self._failure(
                "Missing clearance information",
                errors=[
                    "clearance and required_clearance are required "
                    "(expected output from RequestUnderstandingAgent)"
                ],
            )

        session_valid, session_reason = (
            self._session_validator.is_valid(input_data)
        )

        if not session_valid:
            return self._failure(
                f"Session validation failed: {session_reason}",
                errors=[session_reason],
            )

        clearance_sufficient = clearance >= required_clearance

        role_override_applied = False
        override_reason = ""

        if not clearance_sufficient and role:
            overridden_scopes = ROLE_OVERRIDES.get(role, set())

            if resource_sensitivity in overridden_scopes:
                role_override_applied = True
                override_reason = (
                    f"Role '{role}' has scope override for "
                    f"'{resource_sensitivity}' resources, bypassing "
                    f"raw clearance shortfall"
                )

        now = self._now_fn()
        after_hours = not is_business_hours(now)

        after_hours_flagged = (
            after_hours
            and resource_sensitivity in AFTER_HOURS_SENSITIVE_LEVELS
        )

        data = {
            "clearance_sufficient": (
                clearance_sufficient or role_override_applied
            ),
            "role_override_applied": role_override_applied,
            "after_hours_access": after_hours_flagged,
            "session_valid": True,
        }

        reasoning_parts = ["Session valid"]

        if role_override_applied:
            reasoning_parts.append(override_reason)

        elif clearance_sufficient:
            reasoning_parts.append(
                f"Clearance {clearance} meets or exceeds required "
                f"{required_clearance} - RBAC check passed"
            )

        else:
            reasoning_parts.append(
                f"Clearance {clearance} is below required "
                f"{required_clearance}"
                + (
                    f" (role '{role}' has no applicable override)"
                    if role
                    else ""
                )
            )

        if after_hours_flagged:
            reasoning_parts.append(
                f"Access outside business hours to a "
                f"'{resource_sensitivity}' resource - flagged for risk review"
            )

        return self._success(
            data,
            " | ".join(reasoning_parts),
        )