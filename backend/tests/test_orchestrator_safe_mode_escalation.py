# backend/tests/test_orchestrator_safe_mode_escalation.py

"""
Day 68: closes the safe-mode gap on the escalation-approval path.

SystemAwareRequestProcessor originally gated only
AUTHORIZED -> ACCESS_GRANTED. An approved escalation reaches
ACCESS_GRANTED from MANAGER_REVIEW and was never gated at all.
Also covers two related orchestrator fixes found while reproducing
it: session_expired forwarding and resolve_escalation() with no
decision yet.
"""

from core.orchestrator import RequestPipeline, SystemAwareRequestProcessor, AccessBlockedBySafeModeError
from fsm.governance_fsm import GovernanceFSM
from fsm.recovery_fsm import RecoveryFSM
from fsm.states import RequestState, SystemState
import pytest


ESCALATING_REQUEST = {
    "user_id": "user-003", "resource_id": "resource-003", "session_token": "abc",
}


def _pending_pipeline(request_id):
    pipeline = RequestPipeline()
    result = pipeline.submit_request({**ESCALATING_REQUEST, "request_id": request_id})
    assert result.status == "pending_approval"
    return pipeline


class TestApprovedEscalationGatedBySafeMode:

    def test_approval_during_safe_mode_is_blocked(self):
        pipeline = _pending_pipeline("req-d68-001")
        pipeline.recovery_fsm.state = SystemState.SAFE_MODE_ACTIVE

        result = pipeline.resolve_escalation("req-d68-001", human_decision="approved")

        assert result.status == "blocked_safe_mode"
        assert result.audit_record.final_decision == "PENDING"

    def test_blocked_approval_stays_pending_and_succeeds_after_recovery(self):
        pipeline = _pending_pipeline("req-d68-002")
        pipeline.recovery_fsm.state = SystemState.SAFE_MODE_ACTIVE
        pipeline.resolve_escalation("req-d68-002", human_decision="approved")

        pipeline.recovery_fsm.state = SystemState.SYSTEM_NORMAL
        result = pipeline.resolve_escalation("req-d68-002", human_decision="approved")

        assert result.status == "granted"

    def test_rejection_during_safe_mode_still_denies(self):
        pipeline = _pending_pipeline("req-d68-003")
        pipeline.recovery_fsm.state = SystemState.SAFE_MODE_ACTIVE

        result = pipeline.resolve_escalation("req-d68-003", human_decision="rejected")

        assert result.status == "denied"

    def test_public_readonly_approval_passes_during_safe_mode(self):
        pipeline = RequestPipeline()
        pipeline.submit_request({**ESCALATING_REQUEST, "request_id": "req-d68-004", "is_public_readonly": True})
        pipeline.recovery_fsm.state = SystemState.SAFE_MODE_ACTIVE

        result = pipeline.resolve_escalation("req-d68-004", human_decision="approved")

        assert result.status == "granted"


class TestProcessorGateDirectly:

    def test_manager_review_with_approval_raises_in_safe_mode(self):
        processor = SystemAwareRequestProcessor(RecoveryFSM(initial_state=SystemState.SAFE_MODE_ACTIVE))
        fsm = GovernanceFSM(request_id="req-d68-010", initial_state=RequestState.MANAGER_REVIEW)

        with pytest.raises(AccessBlockedBySafeModeError):
            processor.process(fsm, {"approval_token_valid": True})

        assert fsm.state == RequestState.MANAGER_REVIEW

    def test_manager_review_timeout_is_not_blocked_in_safe_mode(self):
        processor = SystemAwareRequestProcessor(RecoveryFSM(initial_state=SystemState.SAFE_MODE_ACTIVE))
        fsm = GovernanceFSM(request_id="req-d68-011", initial_state=RequestState.MANAGER_REVIEW)

        assert processor.process(fsm, {"timed_out": True}) == RequestState.DENIED_FINAL


class TestRelatedOrchestratorFixes:

    def test_expired_session_is_forwarded_and_rejected(self):
        result = RequestPipeline().submit_request({
            "user_id": "user-001", "resource_id": "resource-001",
            "session_token": "abc", "session_expired": True,
        })
        assert result.status == "error"

    def test_resolve_with_no_decision_and_no_timeout_stays_pending(self):
        pipeline = _pending_pipeline("req-d68-020")

        result = pipeline.resolve_escalation("req-d68-020")

        assert result.status == "error"
        assert "req-d68-020" in pipeline._pending_requests
        assert pipeline.resolve_escalation("req-d68-020", human_decision="approved").status == "granted"