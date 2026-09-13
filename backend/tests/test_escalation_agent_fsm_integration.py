# backend/tests/test_escalation_agent_fsm_integration.py

"""
Full four-agent chain integration tests: RequestUnderstandingAgent ->
AccessValidationAgent -> SecurityRiskAgent -> EscalationAgent ->
GovernanceFSM, covering the complete MANAGER_REVIEW cycle that Day
41's three-agent tests could only reach ESCALATION_REQUIRED for.

This is the second rehearsal for the Day 60-61 orchestrator, and the
first to exercise EscalationAgent's two-phase design (process() at
escalation time, resolve_decision() later) inside a real FSM run.
"""

from datetime import datetime
from agents.request_agent import RequestUnderstandingAgent
from agents.validation_agent import AccessValidationAgent
from agents.security_agent import SecurityRiskAgent
from agents.escalation_agent import EscalationAgent
from core.request_history_tracker import RequestHistoryTracker
from core.geo_anomaly_detector import GeoAnomalyDetector
from core.timeout_tracker import EscalationTimeoutTracker
from fsm.governance_fsm import GovernanceFSM
from fsm.states import RequestState


def run_chain_to_escalation(user_id, resource_id, now_fn, request_id,
                              history=None, geo=None, timeout_tracker=None):
    """
    Runs the first three agents and drives the FSM up to (and
    including) ESCALATION_REQUIRED -> MANAGER_REVIEW, calling
    EscalationAgent.process() along the way. Returns the FSM, the
    escalation agent (so resolve_decision can be called next), and
    the combined context so far.
    """
    request_agent = RequestUnderstandingAgent()
    request_result = request_agent.process({"user_id": user_id, "resource_id": resource_id})
    assert request_result.success, f"RequestAgent failed: {request_result.errors}"

    validation_agent = AccessValidationAgent(now_fn=now_fn)
    combined = dict(request_result.data)
    combined["session_token"] = "valid-token"
    validation_result = validation_agent.process(combined)
    assert validation_result.success, f"ValidationAgent failed: {validation_result.errors}"
    combined.update(validation_result.data)

    security_agent = SecurityRiskAgent(
        history_tracker=history or RequestHistoryTracker(),
        geo_detector=geo or GeoAnomalyDetector(),
    )
    combined["user_id"] = user_id
    security_result = security_agent.process(combined)
    assert security_result.success, f"SecurityAgent failed: {security_result.errors}"
    combined.update(security_result.data)

    fsm = GovernanceFSM(request_id=request_id)
    fsm.transition(combined)  # -> REQUEST_RECEIVED
    fsm.transition(combined)  # -> PARSING_REQUEST
    fsm.transition(combined)  # -> VALIDATING_ACCESS
    state = fsm.transition(combined)  # -> ESCALATION_REQUIRED (assumed)
    assert state == RequestState.ESCALATION_REQUIRED, (
        f"Expected ESCALATION_REQUIRED, got {state.value} - "
        f"risk_score={combined.get('risk_score')}, clearance shortfall check needed"
    )

    escalation_agent = EscalationAgent(timeout_tracker=timeout_tracker or EscalationTimeoutTracker())
    combined["request_id"] = request_id
    escalation_result = escalation_agent.process(combined)
    assert escalation_result.success, f"EscalationAgent failed: {escalation_result.errors}"
    combined.update(escalation_result.data)

    fsm.transition(combined)  # -> MANAGER_REVIEW

    return fsm, escalation_agent, combined


class TestFourAgentChainApprovalPath:

    def test_human_approval_resolves_to_access_granted(self):
        fixed_now = lambda: datetime(2026, 9, 9, 14, 0)
        fsm, escalation_agent, combined = run_chain_to_escalation(
            "user-003", "resource-003", fixed_now, "req-4agent-001",
        )

        decision_result = escalation_agent.resolve_decision(
            "req-4agent-001", human_decision="approved",
        )
        combined.update(decision_result.data)

        final_state = fsm.transition(combined)

        assert final_state == RequestState.ACCESS_GRANTED

        # Continue to closure
        fsm.transition(combined)  # -> AUDIT_LOGGING
        final_state = fsm.transition(combined)  # -> CLOSED
        assert final_state == RequestState.CLOSED


class TestFourAgentChainRejectionPath:

    def test_human_rejection_resolves_to_denied_final(self):
        fixed_now = lambda: datetime(2026, 9, 9, 14, 0)
        fsm, escalation_agent, combined = run_chain_to_escalation(
            "user-003", "resource-003", fixed_now, "req-4agent-002",
        )

        decision_result = escalation_agent.resolve_decision(
            "req-4agent-002", human_decision="rejected",
        )
        combined.update(decision_result.data)

        final_state = fsm.transition(combined)

        assert final_state == RequestState.DENIED_FINAL


class TestFourAgentChainTimeoutPath:

    def test_timeout_with_no_response_resolves_to_denied_final(self):
        from datetime import timedelta

        state = {"now": datetime(2026, 9, 9, 14, 0)}
        fixed_now = lambda: state["now"]
        tracker = EscalationTimeoutTracker(default_timeout_seconds=1800, now_fn=fixed_now)

        fsm, escalation_agent, combined = run_chain_to_escalation(
            "user-003", "resource-003", fixed_now, "req-4agent-003",
            timeout_tracker=tracker,
        )

        state["now"] += timedelta(seconds=1801)

        decision_result = escalation_agent.resolve_decision("req-4agent-003")
        combined.update(decision_result.data)

        final_state = fsm.transition(combined)

        assert final_state == RequestState.DENIED_FINAL


class TestFourAgentChainApproverIsCorrectPerson:

    def test_escalation_routes_to_the_actual_correct_approver_in_full_chain(self):
        """
        Confirms the approver found by EscalationAgent inside the
        full chain is the same person the standalone routing tests
        (Day 43) would predict - the chain doesn't somehow alter
        routing logic.
        """
        fixed_now = lambda: datetime(2026, 9, 9, 14, 0)
        _, _, combined = run_chain_to_escalation(
            "user-003", "resource-003", fixed_now, "req-4agent-004",
        )

        # user-003 (Marcus Webb) reports to user-004 (Sofia Ricci, clearance 3)
        assert combined["approver_user_id"] == "user-004"

    def test_notification_record_present_at_manager_review(self):
        fixed_now = lambda: datetime(2026, 9, 9, 14, 0)
        _, _, combined = run_chain_to_escalation(
            "user-003", "resource-003", fixed_now, "req-4agent-005",
        )

        assert "notification" in combined
        assert combined["notification"].requester_user_id == "user-003"


class TestFourAgentChainFullHistoryTraceable:

    def test_fsm_history_shows_the_complete_seven_step_journey(self):
        fixed_now = lambda: datetime(2026, 9, 9, 14, 0)
        fsm, escalation_agent, combined = run_chain_to_escalation(
            "user-003", "resource-003", fixed_now, "req-4agent-006",
        )

        decision_result = escalation_agent.resolve_decision(
            "req-4agent-006", human_decision="approved",
        )
        combined.update(decision_result.data)
        fsm.transition(combined)
        fsm.transition(combined)
        fsm.transition(combined)

        visited_states = [r.to_state for r in fsm.history]
        expected_journey = [
            RequestState.REQUEST_RECEIVED,
            RequestState.PARSING_REQUEST,
            RequestState.VALIDATING_ACCESS,
            RequestState.ESCALATION_REQUIRED,
            RequestState.MANAGER_REVIEW,
            RequestState.ACCESS_GRANTED,
            RequestState.AUDIT_LOGGING,
            RequestState.CLOSED,
        ]

        assert visited_states == expected_journey