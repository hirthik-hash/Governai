# backend/agents/request_agent.py

"""
Request Understanding Agent (GovernAI Agent 1 of 7).

Takes a structured access request and produces the classification
data GovernanceFSM needs to make a decision. Three input shapes are
supported:

  - Exact lookup:    {"user_id": ..., "resource_id": ...}
  - Fuzzy lookup:     {"user_id": ..., "resource_name": ...}
  - Either + urgency: add "urgency": "low"|"normal"|"high" (default "normal")

Fuzzy resource_name lookups may be genuinely ambiguous (multiple
resources match) - in that case this agent returns success=True with
ambiguity_flag=True and a list of candidate_resource_ids, which feeds
directly into the FSM's CLARIFICATION_REQUESTED loop rather than
guessing which resource was meant.

Users and resources are looked up through an injected Directory (Day 75):
the in-memory seed data by default, or the database.

Deliberately does NOT do free-text NLP or infer urgency/intent from
unstructured input - see Day 26/28 design notes in chat history for
why. All inputs are explicit and structured; if free-text parsing is
ever added, it belongs in a new method or a later agent (candidate
for Phase 4's Ollama-backed Policy Intelligence Agent), not bolted
onto this one.

Day 75 cleanup: this file used to define RequestUnderstandingAgent twice
(the Day 28 urgency change was pasted in below the original instead of
replacing it), so the first class was silently shadowed dead code. There
is now exactly one class, with the behavior the second one had.
"""
from agents.base_agent import BaseAgent, AgentResult
from data.directory import Directory, SeedDirectory

VALID_URGENCY_LEVELS = {"low", "normal", "high"}


def find_resources_by_name(name_query: str) -> list:
    """
    Case-insensitive partial match against the SEED resource names.
    Kept as a module-level function for existing callers; the agent
    itself uses its Directory's find_resources_by_name().
    """
    return SeedDirectory().find_resources_by_name(name_query)


class RequestUnderstandingAgent(BaseAgent):
    """
    First-contact agent: takes a structured request and produces the
    classification data the FSM context needs. Accepts either an
    exact resource_id or a fuzzy resource_name (which may be
    ambiguous), and an optional urgency level. Does not infer
    urgency or intent from free text - all inputs are explicit and
    structured, consistent with this agent's design throughout.
    """

    def __init__(self, directory: Directory = None):
        super().__init__()
        self._directory = directory or SeedDirectory()

    @property
    def agent_name(self) -> str:
        return "request_understanding"

    def process(self, input_data: dict) -> AgentResult:
        user_id = input_data.get("user_id")
        resource_id = input_data.get("resource_id")
        resource_name = input_data.get("resource_name")
        urgency = input_data.get("urgency", "normal")

        if urgency not in VALID_URGENCY_LEVELS:
            return self._failure(
                f"Invalid urgency level: {urgency}",
                errors=[f"urgency must be one of {sorted(VALID_URGENCY_LEVELS)}, got '{urgency}'"],
            )

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
            user = self._directory.get_user(user_id)
        except ValueError:
            return self._failure(
                f"Unknown user_id: {user_id}",
                errors=[f"No user found with id {user_id}"],
            )

        # Prefer exact resource_id when given - unambiguous by definition.
        if resource_id:
            try:
                resource = self._directory.get_resource(resource_id)
            except ValueError:
                return self._failure(
                    f"Unknown resource_id: {resource_id}",
                    errors=[f"No resource found with id {resource_id}"],
                )
            return self._build_success_result(user, resource, urgency)

        # Otherwise, resolve resource_name - may be ambiguous.
        matches = self._directory.find_resources_by_name(resource_name)

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
                    "urgency": urgency,
                },
                reasoning=(
                    f"'{resource_name}' matches {len(matches)} resources "
                    f"({', '.join(candidate_names)}) - clarification needed"
                ),
            )

        # Exactly one match - resolves the same as an exact resource_id.
        return self._build_success_result(user, matches[0], urgency)

    def _build_success_result(self, user, resource, urgency: str) -> AgentResult:
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
            "urgency": urgency,
        }

        reasoning = (
            f"{user.name} ({user.department}, clearance {user.clearance_level}) "
            f"requesting '{resource.name}' ({resource.sensitivity.value}, "
            f"requires clearance {resource.required_clearance}), urgency={urgency}"
        )
        if cross_department:
            reasoning += " [cross-department request]"

        return self._success(data, reasoning)
