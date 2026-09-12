# backend/tests/test_security_agent_fsm_integration.py

"""
Full three-agent chain integration tests: RequestUnderstandingAgent ->
AccessValidationAgent -> SecurityRiskAgent -> GovernanceFSM.

This is the first place all three Phase 2 agents built so far (Days
26-40) are chained together in realistic combinations covering every
FSM outcome, rather than agent-by-agent unit tests. This is a
rehearsal for the Day 60-61 orchestrator, which will formalize this
exact chaining pattern for all 7 agents.
"""

from datetime import datetime
from agents.request_agent import RequestUnderstandingAgent
from agents.validation_agent import AccessValidationAgent
from agents.security_agent import SecurityRiskAgent
from core.request_history_tracker import RequestHistoryTracker
from core.geo_anomaly_detector import GeoAnomalyDetector
from fsm.governance_fsm import GovernanceFSM
from fsm.states import RequestState


def run_full_chain(user_id, resource_id, now_fn=None, history=None, geo=None,
                    session_token="valid-token", location=None):
    """Shared helper: runs the three-agent chain and returns the final FSM state."""
    request_agent = RequestUnderstandingAgent()
    request_result = request_agent.process({
        "user_id": user_id, "resource_id": resource_id,
    })
    assert request_result.success, f"RequestAgent failed: {request_result.errors}"

    validation_agent = AccessValidationAgent(now_fn=now_fn or datetime.now)
    combined = dict(request_result.data)
    combined["session_token"] = session_token
    validation_result = validation_agent.process(combined)
    assert validation_result.success, f"ValidationAgent failed: {validation_result.errors}"
    combined.update(validation_result.data)

    security_agent = SecurityRiskAgent(
        history_tracker=history or RequestHistoryTracker(),
        geo_detector=geo or GeoAnomalyDetector(),
    )
    combined["user_id"] = user_id
    if location:
        combined["location"] = location
    security_result = security_agent.process(combined)
    assert security_result.success, f"SecurityAgent failed: {security_result.errors}"
    combined.update(security_result.data)

    fsm = GovernanceFSM(request_id=f"chain-{user_id}-{resource_id}")
    final_state = fsm.run_until_stuck(combined)
    return final_state, combined, fsm


class TestFullChainReachesAuthorization:

    def test_high_clearance_low_risk_reaches_closed(self):
        # user-007: James Whitfield, CISO, clearance 5
        # resource-001: Employee Handbook, public, requires 0
        fixed_now = lambda: datetime(2026, 9, 9, 14, 0)  # business hours
        final_state, combined, _ = run_full_chain(
            "user-007", "resource-001", now_fn=fixed_now,
        )

        assert final_state == RequestState.CLOSED
        assert combined["risk_score"] < 40

    def test_same_department_business_hours_low_risk(self):
        # user-001 and resource-002 are both engineering
        fixed_now = lambda: datetime(2026, 9, 9, 10, 0)
        final_state, combined, _ = run_full_chain(
            "user-001", "resource-002", now_fn=fixed_now,
        )

        assert final_state == RequestState.CLOSED
        assert combined["cross_department_request"] is False


class TestFullChainReachesEscalation:

    def test_moderate_clearance_shortfall_escalates(self):
        # user-003: Marcus Webb, Financial Analyst, clearance 1
        # resource-003: Q4 Financial Report, restricted, requires 3
        fixed_now = lambda: datetime(2026, 9, 9, 14, 0)
        final_state, combined, _ = run_full_chain(
            "user-003", "resource-003", now_fn=fixed_now,
        )

        assert final_state == RequestState.ESCALATION_REQUIRED
        assert combined["risk_score"] < 40

    def test_cross_department_after_hours_escalates(self):
        # user-001 (engineering) requesting resource-003 (finance),
        # after hours - two moderate factors combined
        fixed_now = lambda: datetime(2026, 9, 9, 23, 0)  # after hours
        final_state, combined, _ = run_full_chain(
            "user-001", "resource-003", now_fn=fixed_now,
        )

        assert final_state == RequestState.ESCALATION_REQUIRED
        assert combined["cross_department_request"] is True
        assert combined["after_hours_access"] is True


class TestFullChainReachesHardDenial:

    def test_blacklisted_user_hard_denies_regardless_of_risk_score(self):
        # user-009 is the seed data's blacklisted user
        fixed_now = lambda: datetime(2026, 9, 9, 14, 0)  # otherwise low-risk conditions
        final_state, combined, _ = run_full_chain(
            "user-009", "resource-001", now_fn=fixed_now,  # public resource, low clearance need
        )

        assert final_state == RequestState.DENIED_FINAL
        assert combined["blacklist_match"] is True

    def test_maxed_risk_factors_hard_denies_via_risk_score(self):
        # Build up enough behavioral history to push risk_score >= 85
        # without relying on blacklist_match this time.
        history = RequestHistoryTracker(failure_threshold=3, rapid_threshold=5)
        for _ in range(3):
            history.record_request("user-008", was_denied=True)
        for _ in range(2):
            history.record_request("user-008", was_denied=False)

        geo = GeoAnomalyDetector()
        geo.record_location("user-008", "US")

        fixed_now = lambda: datetime(2026, 9, 9, 23, 0)  # after hours
        final_state, combined, _ = run_full_chain(
            "user-008",  # Rina Sato, Junior Developer, clearance 0
            "resource-005",  # Salary Records, top_secret, requires 5 - cross-dept too
            now_fn=fixed_now, history=history, geo=geo, location="RU",
        )

        assert final_state == RequestState.DENIED_FINAL
        assert combined["risk_score"] >= 85


class TestFullChainAgentDataFlowsCorrectlyThroughAllThreeStages:

    def test_every_expected_field_present_in_final_combined_context(self):
        """
        Confirms the full chain produces every field GovernanceFSM
        actually consumes, plus the descriptive fields from all three
        agents - nothing silently dropped when merging three agents'
        outputs together.
        """
        fixed_now = lambda: datetime(2026, 9, 9, 14, 0)
        _, combined, _ = run_full_chain("user-001", "resource-001", now_fn=fixed_now)

        # Fields the FSM itself reads
        for fsm_field in ["clearance", "required_clearance", "risk_score",
                           "blacklist_match", "ambiguity_flag"]:
            assert fsm_field in combined, f"Missing FSM-consumed field: {fsm_field}"

        # Descriptive fields from each agent, useful for later
        # Explainability Center work even though the FSM doesn't read them
        for descriptive_field in ["cross_department_request", "after_hours_access",
                                    "risk_level", "recommendation", "risk_triggers"]:
            assert descriptive_field in combined, f"Missing descriptive field: {descriptive_field}"

    def test_fsm_history_reflects_the_actual_computed_risk_score(self):
        """
        The FSM's own transition record should show the SAME
        risk_score the SecurityRiskAgent computed - not a stale or
        default value, confirming the merge actually took effect
        before the FSM ran.
        """
        fixed_now = lambda: datetime(2026, 9, 9, 23, 0)
        final_state, combined, fsm = run_full_chain(
            "user-003", "resource-003", now_fn=fixed_now,
        )

        # The context snapshot recorded at the VALIDATING_ACCESS step
        # should show the same risk_score used to make the decision.
        validating_step = next(
            r for r in fsm.history if r.from_state == RequestState.VALIDATING_ACCESS
        )
        assert validating_step.context_snapshot["risk_score"] == combined["risk_score"]