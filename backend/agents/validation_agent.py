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

This agent does not itself grant or deny access - it enriches the
context with a `role_override_applied` flag and reasoning that later
agents/humans can inspect, while the FSM remains the sole authority
on the actual decision.
"""

from agents.base_agent import BaseAgent, AgentResult

# Roles that bypass department-scope concerns for a specific resource
# sensitivity, regardless of their raw numeric clearance level. Kept
# small and explicit on purpose - this is not a general permissions
# matrix, just the one clear override case worth encoding today.
ROLE_OVERRIDES = {
    "CISO": {"security", "top_secret"},
}


class AccessValidationAgent(BaseAgent):
    """
    Permission gatekeeper: takes the output of RequestUnderstandingAgent
    and produces an explicit RBAC explanation, applying any role-based
    overrides. Does not change clearance/required_clearance in the
    context - the FSM remains the actual decision-maker.
    """

    @property
    def agent_name(self) -> str:
        return "access_validation"

    def process(self, input_data: dict) -> AgentResult:
        clearance = input_data.get("clearance")
        required_clearance = input_data.get("required_clearance")
        role = input_data.get("role")

        if clearance is None or required_clearance is None:
            return self._failure(
                "Missing clearance information",
                errors=["clearance and required_clearance are required "
                        "(expected output from RequestUnderstandingAgent)"],
            )

        clearance_sufficient = clearance >= required_clearance

        role_override_applied = False
        override_reason = ""

        if not clearance_sufficient and role:
            resource_sensitivity = input_data.get("resource_sensitivity")
            overridden_scopes = ROLE_OVERRIDES.get(role, set())
            if resource_sensitivity in overridden_scopes:
                role_override_applied = True
                override_reason = (
                    f"Role '{role}' has scope override for "
                    f"'{resource_sensitivity}' resources, bypassing "
                    f"raw clearance shortfall"
                )

        data = {
            "clearance_sufficient": clearance_sufficient or role_override_applied,
            "role_override_applied": role_override_applied,
        }

        if role_override_applied:
            reasoning = override_reason
        elif clearance_sufficient:
            reasoning = (
                f"Clearance {clearance} meets or exceeds required "
                f"{required_clearance} - RBAC check passed"
            )
        else:
            reasoning = (
                f"Clearance {clearance} is below required "
                f"{required_clearance}"
                + (f" (role '{role}' has no applicable override)" if role else "")
            )

        return self._success(data, reasoning)