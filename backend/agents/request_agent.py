# backend/agents/request_agent.py

from agents.base_agent import BaseAgent, AgentResult
from data.seed_data import get_user, get_resource


class RequestUnderstandingAgent(BaseAgent):
    """
    First-contact agent: takes a structured request (user_id,
    resource_id) and produces the classification data the FSM
    context needs - required_clearance, department match info,
    and an ambiguity flag.

    Deliberately does NOT do free-text NLP parsing yet - this agent
    assumes requests arrive as structured IDs (e.g. from an internal
    form or API), which is realistic for GovernAI's target use case.
    """

    @property
    def agent_name(self) -> str:
        return "request_understanding"

    def process(self, input_data: dict) -> AgentResult:
        user_id = input_data.get("user_id")
        resource_id = input_data.get("resource_id")

        if not user_id or not resource_id:
            return self._failure(
                "Missing required fields",
                errors=["user_id and resource_id are both required"],
            )

        try:
            user = get_user(user_id)
        except ValueError:
            return self._failure(
                f"Unknown user_id: {user_id}",
                errors=[f"No user found with id {user_id}"],
            )

        try:
            resource = get_resource(resource_id)
        except ValueError:
            return self._failure(
                f"Unknown resource_id: {resource_id}",
                errors=[f"No resource found with id {resource_id}"],
            )

        cross_department = user.department != resource.department

        data = {
            "clearance": user.clearance_level,
            "required_clearance": resource.required_clearance,
            "blacklist_match": user.is_blacklisted,
            "ambiguity_flag": False,  # placeholder until free-text parsing exists
            "cross_department_request": cross_department,
            "requester_department": user.department,
            "resource_department": resource.department,
            "resource_sensitivity": resource.sensitivity.value,
        }

        reasoning = (
            f"{user.name} ({user.department}, clearance {user.clearance_level}) "
            f"requesting '{resource.name}' ({resource.sensitivity.value}, "
            f"requires clearance {resource.required_clearance})"
        )
        if cross_department:
            reasoning += " [cross-department request]"

        return self._success(data, reasoning)