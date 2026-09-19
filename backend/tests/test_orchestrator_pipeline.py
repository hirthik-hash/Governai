# backend/tests/test_orchestrator_pipeline.py

"""
Tests RequestPipeline as a class, not as another hand-built agent
chain - a caller using only submit_request()/resolve_escalation()
should get correct results without knowing anything about the six
agents underneath.
"""

from core.orchestrator import RequestPipeline
from fsm.states import SystemState
from fsm.recovery_fsm import RecoveryFSM


class TestPipelineDirectOutcomes:

    def test_high_clearance_low_risk_request_is_granted(self):
        pipeline = RequestPipeline()
        result = pipeline.submit_request({
            "user_id": "user-007", "resource_id": "resource-001",
            "session_token": "abc",
        })

        assert result.status == "granted"
        assert result.audit_record is not None
        assert result.audit_record.final_decision == "GRANTED"

    def test_blacklisted_user_is_denied(self):
        pipeline = RequestPipeline()
        result = pipeline.submit_request({
            "user_id": "user-009", "resource_id": "resource-001",
            "session_token": "abc",
        })

        assert result.status == "denied"
        assert result.audit_record.final_decision == "DENIED"

    def test_ambiguous_resource_name_returns_clarification_needed(self):
        pipeline = RequestPipeline()
        result = pipeline.submit_request({
            "user_id": "user-001", "resource_name": "e", "session_token": "abc",
        })

        assert result.status == "clarification_needed"
        assert len(result.candidate_resource_ids) > 1

    def test_missing_session_token_is_an_error(self):
        pipeline = RequestPipeline()
        result = pipeline.submit_request({
            "user_id": "user-001", "resource_id": "resource-001",
        })

        assert result.status == "error"


class TestPipelineEscalationFullCycle:

    def test_escalation_then_approval_grants_access(self):
        pipeline = RequestPipeline()

        submit_result = pipeline.submit_request({
            "request_id": "req-pipeline-001",
            "user_id": "user-003", "resource_id": "resource-003",
            "session_token": "abc",
        })
        assert submit_result.status == "pending_approval"
        assert submit_result.notification is not None

        resolve_result = pipeline.resolve_escalation("req-pipeline-001", human_decision="approved")

        assert resolve_result.status == "granted"
        assert resolve_result.audit_record.approver_user_id == "user-004"

    def test_escalation_then_rejection_denies_access(self):
        pipeline = RequestPipeline()

        pipeline.submit_request({
            "request_id": "req-pipeline-002",
            "user_id": "user-003", "resource_id": "resource-003",
            "session_token": "abc",
        })

        resolve_result = pipeline.resolve_escalation("req-pipeline-002", human_decision="rejected")

        assert resolve_result.status == "denied"

    def test_resolving_unknown_request_id_is_an_error(self):
        pipeline = RequestPipeline()
        result = pipeline.resolve_escalation("never-submitted")

        assert result.status == "error"

    def test_resolved_request_is_removed_from_pending(self):
        pipeline = RequestPipeline()
        pipeline.submit_request({
            "request_id": "req-pipeline-003",
            "user_id": "user-003", "resource_id": "resource-003",
            "session_token": "abc",
        })

        pipeline.resolve_escalation("req-pipeline-003", human_decision="approved")

        # Resolving again should now fail - it's no longer pending
        second_attempt = pipeline.resolve_escalation("req-pipeline-003", human_decision="approved")
        assert second_attempt.status == "error"


class TestPipelineSafeModeIntegration:

    def test_request_blocked_during_real_safe_mode(self):
        shared_fsm = RecoveryFSM(initial_state=SystemState.SAFE_MODE_ACTIVE)
        pipeline = RequestPipeline(recovery_fsm=shared_fsm)

        result = pipeline.submit_request({
            "user_id": "user-007", "resource_id": "resource-001",
            "session_token": "abc",
        })

        assert result.status == "blocked_safe_mode"
        assert result.audit_record is not None
        assert result.audit_record.final_decision == "PENDING"

    def test_public_readonly_request_still_granted_during_safe_mode(self):
        shared_fsm = RecoveryFSM(initial_state=SystemState.SAFE_MODE_ACTIVE)
        pipeline = RequestPipeline(recovery_fsm=shared_fsm)

        result = pipeline.submit_request({
            "user_id": "user-007", "resource_id": "resource-001",
            "session_token": "abc", "is_public_readonly": True,
        })

        assert result.status == "granted"


class TestMultipleConcurrentPendingRequests:

    def test_two_escalated_requests_resolved_independently(self):
        pipeline = RequestPipeline()

        pipeline.submit_request({
            "request_id": "req-concurrent-A", "user_id": "user-003",
            "resource_id": "resource-003", "session_token": "abc",
        })
        pipeline.submit_request({
            "request_id": "req-concurrent-B", "user_id": "user-001",
            "resource_id": "resource-004", "session_token": "abc",
        })

        result_a = pipeline.resolve_escalation("req-concurrent-A", human_decision="approved")
        result_b = pipeline.resolve_escalation("req-concurrent-B", human_decision="rejected")

        assert result_a.status == "granted"
        assert result_b.status == "denied"