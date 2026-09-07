# backend/agents/request_agent.py — full replacement

from agents.base_agent import BaseAgent, AgentResult
from data.seed_data import get_user, get_resource, SEED_RESOURCES


def find_resources_by_name(name_query: str) -> list:
    """
    Case-insensitive partial match against resource names. Returns
    every resource whose name contains name_query - may be zero,
    one, or many results, which is exactly the ambiguity surface
    we want to detect.
    """
    query_lower = name_query.lower()
    return [r for r in SEED_RESOURCES if query_lower in r.name.lower()]


class RequestUnderstandingAgent(BaseAgent):
    """
    First-contact agent: takes a structured request and produces the
    classification data the FSM context needs. Accepts either an
    exact resource_id or a fuzzy resource_name - the latter may be
    genuinely ambiguous, which this agent detects and reports rather
    than guessing.
    """

    @property
    def agent_name(self) -> str:
        return "request_understanding"

    def process(self, input_data: dict) -> AgentResult:
        user_id = input_data.get("user_id")
        resource_id = input_data.get("resource_id")
        resource_name = input_data.get("resource_name")

        if not user_id:
            return self._failure(
                "Missing required field",
                errors=["user_id is required"],
            )

        if not resource_id and not resource_name:
            return self._failure(
                "Missing required field",
                errors=["either resource_id or resource_name is required"],
            )

        try:
            user = get_user(user_id)
        except ValueError:
            return self._failure(
                f"Unknown user_id: {user_id}",
                errors=[f"No user found with id {user_id}"],
            )

        # Prefer exact resource_id when given - unambiguous by definition.
        if resource_id:
            try:
                resource = get_resource(resource_id)
            except ValueError:
                return self._failure(
                    f"Unknown resource_id: {resource_id}",
                    errors=[f"No resource found with id {resource_id}"],
                )
            return self._build_success_result(user, resource)

        # Otherwise, resolve resource_name - may be ambiguous.
        matches = find_resources_by_name(resource_name)

        if len(matches) == 0:
            return self._failure(
                f"No resource matches name: {resource_name}",
                errors=[f"No resource found matching '{resource_name}'"],
            )

        if len(matches) > 1:
            candidate_ids = [r.id for r in matches]
            candidate_names = [r.name for r in matches]
            return self._success(
                data={
                    "ambiguity_flag": True,
                    "clearance": user.clearance_level,
                    "blacklist_match": user.is_blacklisted,
                    "candidate_resource_ids": candidate_ids,
                },
                reasoning=(
                    f"'{resource_name}' matches {len(matches)} resources "
                    f"({', '.join(candidate_names)}) - clarification needed"
                ),
            )

        # Exactly one match - resolves the same as an exact resource_id.
        return self._build_success_result(user, matches[0])

    def _build_success_result(self, user, resource) -> AgentResult:
        cross_department = user.department != resource.department

        data = {
            "clearance": user.clearance_level,
            "required_clearance": resource.required_clearance,
            "blacklist_match": user.is_blacklisted,
            "ambiguity_flag": False,
            "cross_department_request": cross_department,
            "requester_department": user.department,
            "resource_department": resource.department,
            "resource_sensitivity": resource.sensitivity.value,
            "resolved_resource_id": resource.id,
        }

        reasoning = (
            f"{user.name} ({user.department}, clearance {user.clearance_level}) "
            f"requesting '{resource.name}' ({resource.sensitivity.value}, "
            f"requires clearance {resource.required_clearance})"
        )
        if cross_department:
            reasoning += " [cross-department request]"

        return self._success(data, reasoning)