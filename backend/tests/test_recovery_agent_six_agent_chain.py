# backend/tests/test_recovery_agent_six_agent_chain.py

"""
Six-agent chain integration: RequestUnderstandingAgent ->
AccessValidationAgent -> SecurityRiskAgent -> EscalationAgent (when
needed) -> AuditComplianceAgent, with FailureRecoveryAgent
determining system health throughout. The last full-chain rehearsal
before Day 60's orchestrator formalizes this pattern across all 7
agents.

Also establishes what an AuditRecord looks like for a request that
never reached a normal FSM terminal state - blocked by the
safe-mode gate instead - which no prior test has exercised.
"""

from datetime import datetime
from agents.request_agent import RequestUnderstandingAgent
from agents.validation_agent import AccessValidationAgent
from agents.security_agent import SecurityRiskAgent
from agents.escalation_agent import EscalationAgent
from agents.audit_agent import AuditComplianceAgent
from agents.recovery_agent import FailureRecoveryAgent, HealthCheckResult
from core.request_history_tracker import RequestHistoryTracker
from core.geo_anomaly_detector import GeoAnomalyDetector
from fsm.recovery_fsm import RecoveryFSM
from fsm.governance_fsm import GovernanceFSM
from fsm.states import RequestState, SystemState
from core.orchestrator import SystemAwareRequestProcessor, AccessBlockedBySafeModeError


class TestSixAgentChainHealthySystemFullLifecycle:

    def test_authorized_request_produces_granted_audit_record(self):
        recovery_agent = FailureRecoveryAgent()
        health_result = recovery_agent.evaluate_and_transition()
        assert health_result.data["overall_healthy"] is True

        request_agent = RequestUnderstandingAgent()
        request_result = request_agent.process({
            "user_id": "user-007", "resource_id": "resource-001",
        })

        fixed_now = lambda: datetime(2026, 9, 9, 14, 0)
        validation_agent = AccessValidationAgent(now_fn=fixed_now)
        combined = dict(request_result.data)
        combined["session_token"] = "abc"
        validation_result = validation_agent.process(combined)
        combined.update(validation_result.data)

        security_agent = SecurityRiskAgent()
        combined["user_id"] = "user-007"
        security_result = security_agent.process(combined)
        combined.update(security_result.data)

        request_fsm = GovernanceFSM(request_id="req-6chain-001")
        final_state = request_fsm.run_until_stuck(combined)
        assert final_state == RequestState.CLOSED

        audit_agent = AuditComplianceAgent()
        audit_result = audit_agent.process({
            "request_id": "req-6chain-001", "user_id": "user-007",
            "resolved_resource_id": combined.get("resolved_resource_id"),
            "final_fsm_state": final_state.value,
            "risk_score": combined["risk_score"], "risk_level": combined["risk_level"],
            "agent_results": [request_result, validation_result, security_result],
        })

        record = audit_result.data["audit_record"]
        assert record.final_decision == "GRANTED"
        assert len(record.agent_reasoning_trail) == 3

    def test_escalated_and_approved_request_produces_granted_audit_record_with_approver(self):
        recovery_agent = FailureRecoveryAgent()
        recovery_agent.evaluate_and_transition()

        request_agent = RequestUnderstandingAgent()
        request_result = request_agent.process({
            "user_id": "user-003", "resource_id": "resource-003",
        })

        fixed_now = lambda: datetime(2026, 9, 9, 14, 0)
        validation_agent = AccessValidationAgent(now_fn=fixed_now)
        combined = dict(request_result.data)
        combined["session_token"] = "abc"
        validation_result = validation_agent.process(combined)
        combined.update(validation_result.data)

        security_agent = SecurityRiskAgent()
        combined["user_id"] = "user-003"
        security_result = security_agent.process(combined)
        combined.update(security_result.data)

        request_fsm = GovernanceFSM(request_id="req-6chain-002")
        request_fsm.transition(combined)
        request_fsm.transition(combined)
        request_fsm.transition(combined)
        state = request_fsm.transition(combined)
        assert state == RequestState.ESCALATION_REQUIRED

        escalation_agent = EscalationAgent()
        combined["request_id"] = "req-6chain-002"
        escalation_result = escalation_agent.process(combined)
        combined.update(escalation_result.data)
        request_fsm.transition(combined)  # -> MANAGER_REVIEW

        decision_result = escalation_agent.resolve_decision(
            "req-6chain-002", human_decision="approved",
        )
        combined.update(decision_result.data)
        request_fsm.transition(combined)  # -> ACCESS_GRANTED
        request_fsm.transition(combined)  # -> AUDIT_LOGGING
        final_state = request_fsm.transition(combined)  # -> CLOSED

        audit_agent = AuditComplianceAgent()
        audit_result = audit_agent.process({
            "request_id": "req-6chain-002", "user_id": "user-003",
            "final_fsm_state": final_state.value,
            "approver_user_id": combined["approver_user_id"],
            "risk_score": combined["risk_score"], "risk_level": combined["risk_level"],
            "agent_results": [
                request_result, validation_result, security_result,
                escalation_result, decision_result,
            ],
        })

        record = audit_result.data["audit_record"]
        assert record.final_decision == "GRANTED"
        assert record.approver_user_id == "user-004"
        assert len(record.agent_reasoning_trail) == 5


class TestSixAgentChainSafeModeBlockedRequest:

    def test_blocked_request_produces_a_meaningful_audit_record_not_a_crash(self):
        """
        Establishes the honest answer to a question no prior test
        asked: what does an AuditRecord look like for a request that
        never reached a normal FSM terminal state because it was
        blocked by the safe-mode gate? Uses a synthetic
        final_fsm_state string ('blocked_safe_mode') rather than
        forcing an FSM state value that doesn't exist, since the
        block happens OUTSIDE the FSM's own state machine (it's
        SystemAwareRequestProcessor's gate, not an FSM transition).
        """
        shared_recovery_fsm = RecoveryFSM(initial_state=SystemState.DEGRADED_WARNING)

        def failing_integrity() -> HealthCheckResult:
            return HealthCheckResult(check_name="fsm_integrity", healthy=False, detail="simulated break")

        recovery_agent = FailureRecoveryAgent(checks=[failing_integrity], recovery_fsm=shared_recovery_fsm)
        recovery_agent.evaluate_and_transition()
        assert shared_recovery_fsm.is_safe_mode() is True

        request_agent = RequestUnderstandingAgent()
        request_result = request_agent.process({
            "user_id": "user-007", "resource_id": "resource-001",
        })

        fixed_now = lambda: datetime(2026, 9, 9, 14, 0)
        validation_agent = AccessValidationAgent(now_fn=fixed_now)
        combined = dict(request_result.data)
        combined["session_token"] = "abc"
        validation_result = validation_agent.process(combined)
        combined.update(validation_result.data)

        security_agent = SecurityRiskAgent()
        combined["user_id"] = "user-007"
        security_result = security_agent.process(combined)
        combined.update(security_result.data)

        processor = SystemAwareRequestProcessor(shared_recovery_fsm)
        request_fsm = GovernanceFSM(request_id="req-6chain-blocked-001")

        block_message = ""
        try:
            processor.process_until_stuck(request_fsm, combined)
        except AccessBlockedBySafeModeError as e:
            block_message = str(e)

        assert block_message  # confirms the block actually happened

        audit_agent = AuditComplianceAgent()
        audit_result = audit_agent.process({
            "request_id": "req-6chain-blocked-001", "user_id": "user-007",
            "final_fsm_state": "blocked_safe_mode",  # synthetic - not a real FSM state
            "risk_score": combined["risk_score"], "risk_level": combined["risk_level"],
            "agent_results": [
                request_result, validation_result, security_result,
                recovery_agent.process({}),
            ],
        })

        record = audit_result.data["audit_record"]
        assert record.final_decision == "PENDING"  # honest: no GRANTED/DENIED mapping exists for this
        assert "blocked_safe_mode" in record.final_fsm_state
        assert len(record.agent_reasoning_trail) == 4