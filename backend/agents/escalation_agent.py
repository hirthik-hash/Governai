# backend/agents/escalation_agent.py

"""
Escalation Agent (GovernAI Agent 4 of 7).

Routes an access request that needs human approval to the correct
approver, per the roadmap's fixed hierarchy:

  Employee -> TeamLead (clearance 1->2)
  TeamLead -> Manager (clearance 2->3)
  Manager -> Director (clearance 3->4)
  Director -> CISO (clearance 4->5, top-secret)

Today's piece (Day 43) is routing only: given a requester's user_id,
find the correct approver by walking the reports_to chain (added to
seed_data.py this same day) until reaching someone with sufficient
clearance to approve the resource, or the top of the chain (CISO).

Notification generation, approval timeout wiring, and
approval/rejection/timeout handling are NOT part of this file yet -
those are Days 44-48.
"""

from agents.base_agent import BaseAgent, AgentResult
from data.seed_data import get_user


class NoApproverFoundError(Exception):
    """Raised only for a genuine data integrity problem - a reports_to
    chain that doesn't terminate (e.g. a cycle) or points to a
    nonexistent user. Not raised for normal 'reached the top' cases."""
    pass


class EscalationAgent(BaseAgent):
    """
    Determines the correct approver for an escalated request by
    walking the requester's reports_to chain until finding someone
    with clearance >= required_clearance, or reaching the top
    (a user with no reports_to, i.e. the CISO).
    """

    MAX_CHAIN_DEPTH = 10  # safety limit against cyclic reports_to data

    @property
    def agent_name(self) -> str:
        return "escalation"

    def process(self, input_data: dict) -> AgentResult:
        user_id = input_data.get("user_id")
        required_clearance = input_data.get("required_clearance")

        if not user_id or required_clearance is None:
            return self._failure(
                "Missing required fields",
                errors=["user_id and required_clearance are required"],
            )

        try:
            requester = get_user(user_id)
        except ValueError:
            return self._failure(
                f"Unknown user_id: {user_id}",
                errors=[f"No user found with id {user_id}"],
            )

        try:
            approver = self._find_approver(requester, required_clearance)
        except NoApproverFoundError as e:
            return self._failure(str(e), errors=[str(e)])

        chain_reached_top = approver.reports_to == ""

        data = {
            "approver_user_id": approver.id,
            "approver_name": approver.name,
            "approver_role": approver.role,
            "approver_clearance": approver.clearance_level,
            "escalation_reached_top_of_chain": chain_reached_top,
        }

        reasoning = (
            f"Escalation for {requester.name}'s request routed to "
            f"{approver.name} ({approver.role}, clearance {approver.clearance_level})"
        )
        if chain_reached_top:
            reasoning += " - reached top of chain"

        return self._success(data, reasoning)

    def _find_approver(self, requester, required_clearance: int):
        """
        Walks the reports_to chain starting from requester's own
        manager (not the requester themselves - you can't approve
        your own request), stopping at the first person with
        sufficient clearance, or at the top of the chain if nobody
        in the chain has enough (the CISO is the ultimate approver
        regardless of their clearance relative to required_clearance,
        since there's nowhere higher to escalate to).
        """
        current = requester
        depth = 0

        while True:
            if not current.reports_to:
                # Reached the top of the chain (e.g. the CISO) with
                # no higher clearance available - they're the final
                # approver by default, whatever their own clearance is.
                return current

            depth += 1
            if depth > self.MAX_CHAIN_DEPTH:
                raise NoApproverFoundError(
                    f"reports_to chain exceeded {self.MAX_CHAIN_DEPTH} levels "
                    f"starting from {requester.id} - possible cycle in seed data"
                )

            try:
                current = get_user(current.reports_to)
            except ValueError:
                raise NoApproverFoundError(
                    f"{current.id if hasattr(current, 'id') else 'unknown'}'s "
                    f"reports_to points to nonexistent user"
                )

            if current.clearance_level >= required_clearance:
                return current