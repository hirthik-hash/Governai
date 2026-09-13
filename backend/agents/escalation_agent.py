# backend/agents/escalation_agent.py

"""
Escalation Agent (GovernAI Agent 4 of 7).

Two distinct responsibilities, at two distinct points in time:

  1. process() (Days 43-44) - given a newly-escalated request:
     a. Finds the correct approver by walking the requester's
        reports_to chain (added to seed_data.py this same block),
        skipping anyone without sufficient clearance, stopping at
        the first qualified person, or falling back to the top of
        the chain (the CISO) if nobody qualifies. The CISO is always
        the final approver by default - there's nowhere higher to
        escalate to, regardless of whether their own clearance
        nominally "covers" an extreme requirement (Day 43).
     b. Records the escalation via an injected
        EscalationTimeoutTracker (built Day 14, unused for 30 days
        until this agent), starting its timeout clock, and produces
        a NotificationRecord - a structured, loggable record, NOT a
        real sent message, since no email/Slack infrastructure
        exists yet (Day 44).

  2. resolve_decision() (Day 45) - given a request_id already
     escalated via process(), determines the outcome and returns the
     exact three FSM-ready flags GovernanceFSM's MANAGER_REVIEW
     transitions require: approval_token_valid, rejected, timed_out.
     Always exactly one is True, guaranteed mutually exclusive, so
     this can never trigger the FSM's AmbiguousTransitionError.
     Called either with a real human_decision ("approved"/"rejected")
     or with none, in which case the timeout tracker is consulted.

     DELIBERATE POLICY (found and locked in during Day 46's
     robustness pass): an explicit human_decision is ALWAYS honored,
     even if the timeout tracker would separately report the request
     as already timed out - resolve_decision() never checks the
     tracker when human_decision is provided. A late-but-real human
     approval overrides an expired timeout. This is a product policy
     choice worth revisiting deliberately later, not an accidental
     behavior.

Self-approval edge case (Day 46): if the CISO themselves needs
escalation, they are immediately their own approver (reports_to=""
means the chain has nowhere else to go) - correct given there is
nobody higher, not a bug.

This agent does not check blacklist_match - hard-denial for
blacklisted users happens in the FSM before ESCALATION_REQUIRED is
ever reached, so this agent correctly has no opinion on it (Day 46).

Full four-agent chain integration (RequestUnderstandingAgent ->
AccessValidationAgent -> SecurityRiskAgent -> EscalationAgent ->
GovernanceFSM), covering the complete MANAGER_REVIEW cycle through
approval, rejection, and timeout to final FSM states, is in
tests/test_escalation_agent_fsm_integration.py (Day 47).
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from agents.base_agent import BaseAgent, AgentResult
from data.seed_data import get_user
from core.timeout_tracker import EscalationTimeoutTracker, UnknownEscalationError


class NoApproverFoundError(Exception):
    pass


@dataclass
class NotificationRecord:
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
    Determines the correct approver for an escalated request, records
    it (starting the timeout clock), and later resolves the outcome
    into the exact flags GovernanceFSM's MANAGER_REVIEW state expects.
    """

    MAX_CHAIN_DEPTH = 10

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

    def resolve_decision(self, request_id: str, human_decision: str = None) -> AgentResult:
        """
        Determines the outcome for a request already escalated via
        process(). human_decision, if given, must be "approved" or
        "rejected" - a real human response. If human_decision is
        None, checks the timeout tracker instead.

        Always returns exactly one of the three outcomes as True,
        the other two False - never an ambiguous combination, since
        GovernanceFSM's rulebook requires exactly one matching
        transition condition.
        """
        VALID_DECISIONS = {"approved", "rejected"}

        if human_decision is not None and human_decision not in VALID_DECISIONS:
            return self._failure(
                f"Invalid human_decision: {human_decision}",
                errors=[f"human_decision must be one of {sorted(VALID_DECISIONS)} or None"],
            )

        if human_decision == "approved":
            return self._success(
                {"approval_token_valid": True, "rejected": False, "timed_out": False},
                f"Request {request_id} approved by human decision",
            )

        if human_decision == "rejected":
            return self._success(
                {"approval_token_valid": False, "rejected": True, "timed_out": False},
                f"Request {request_id} rejected by human decision",
            )

        # No human decision yet - check the timeout tracker.
        try:
            is_timed_out = self._timeout_tracker.is_timed_out(request_id)
        except UnknownEscalationError:
            return self._failure(
                f"No escalation on record for request {request_id}",
                errors=[f"resolve_decision called for {request_id} before process() escalated it"],
            )

        if is_timed_out:
            return self._success(
                {"approval_token_valid": False, "rejected": False, "timed_out": True},
                f"Request {request_id} timed out awaiting approval",
            )

        return self._success(
            {"approval_token_valid": False, "rejected": False, "timed_out": False},
            f"Request {request_id} still awaiting approval, not yet timed out",
        )

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