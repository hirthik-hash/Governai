# backend/agents/escalation_agent.py

"""
Escalation Agent (GovernAI Agent 4 of 7).

Routes an access request that needs human approval to the correct
approver (Day 43: walks the reports_to chain from seed_data.py),
then records the escalation and starts its approval timeout clock
(Day 44: via an injected EscalationTimeoutTracker, built Day 14 and
unused until now).

This agent produces a NOTIFICATION RECORD, not a real sent
notification - there is no email/Slack infrastructure yet (that
would be Phase 3+). The notification record is structured and
loggable, which is the honest scope for what exists today; wiring
it to a real channel later is a change to how the record gets
delivered, not to this agent's routing/timeout logic.

Approval/rejection/timeout OUTCOME HANDLING (turning a timeout or a
human decision into FSM context flags) is Days 45-46, not this file.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from agents.base_agent import BaseAgent, AgentResult
from data.seed_data import get_user
from core.timeout_tracker import EscalationTimeoutTracker


class NoApproverFoundError(Exception):
    """Raised only for a genuine data integrity problem - a reports_to
    chain that doesn't terminate (e.g. a cycle) or points to a
    nonexistent user. Not raised for normal 'reached the top' cases."""
    pass


@dataclass
class NotificationRecord:
    """
    Structured record of an escalation notification. Not a real sent
    message - Phase 3+ would deliver this via a real channel. This is
    the honest, loggable shape of what such a delivery would contain.
    """
    request_id: str
    approver_user_id: str
    approver_name: str
    requester_user_id: str
    requester_name: str
    resource_summary: str
    sent_at: str
    timeout_seconds: int


class EscalationAgent(BaseAgent):
    """
    Determines the correct approver for an escalated request, then
    records the escalation (starting its timeout clock via an
    injected EscalationTimeoutTracker) and produces a structured
    notification record.
    """

    MAX_CHAIN_DEPTH = 10  # safety limit against cyclic reports_to data

    def __init__(self, timeout_tracker: EscalationTimeoutTracker = None):
        super().__init__()
        self._timeout_tracker = timeout_tracker or EscalationTimeoutTracker()

    @property
    def agent_name(self) -> str:
        return "escalation"

    def process(self, input_data: dict) -> AgentResult:
        user_id = input_data.get("user_id")
        required_clearance = input_data.get("required_clearance")
        request_id = input_data.get("request_id", "unknown-request")
        resource_summary = input_data.get("resolved_resource_id", "unspecified resource")

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

        # Record the escalation - this starts the timeout clock.
        self._timeout_tracker.record_escalation_sent(request_id)

        notification = NotificationRecord(
            request_id=request_id,
            approver_user_id=approver.id,
            approver_name=approver.name,
            requester_user_id=requester.id,
            requester_name=requester.name,
            resource_summary=resource_summary,
            sent_at=datetime.now(timezone.utc).isoformat(),
            timeout_seconds=self._timeout_tracker.default_timeout_seconds,
        )

        data = {
            "approver_user_id": approver.id,
            "approver_name": approver.name,
            "approver_role": approver.role,
            "approver_clearance": approver.clearance_level,
            "escalation_reached_top_of_chain": chain_reached_top,
            "escalation_sent": True,
            "notification": notification,
        }

        reasoning = (
            f"Escalation for {requester.name}'s request routed to "
            f"{approver.name} ({approver.role}) - notification sent, "
            f"timeout window {notification.timeout_seconds}s"
        )
        if chain_reached_top:
            reasoning += " - reached top of chain"

        return self._success(data, reasoning)

    def _find_approver(self, requester, required_clearance: int):
        current = requester
        depth = 0

        while True:
            if not current.reports_to:
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
                    f"{current.id}'s reports_to points to nonexistent user"
                )

            if current.clearance_level >= required_clearance:
                return current