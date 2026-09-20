# backend/tests/test_orchestrator_day70.py

"""
Day 70: RequestPipeline gains a duplicate-id guard, public read access
to pending escalations, and an in-memory audit record store.
"""

from core.orchestrator import RequestPipeline
from fsm.states import SystemState

ESCALATING = {"user_id": "user-003", "resource_id": "resource-003", "session_token": "abc"}
GRANTED = {"user_id": "user-007", "resource_id": "resource-001", "session_token": "abc"}


class TestDuplicateRequestIdGuard:

    def test_duplicate_of_a_pending_request_is_rejected(self):
        pipeline = RequestPipeline()
        pipeline.submit_request({**ESCALATING, "request_id": "req-dup-001"})

        second = pipeline.submit_request({**ESCALATING, "request_id": "req-dup-001"})

        assert second.status == "error"
        assert "Duplicate request_id" in second.errors[0]

    def test_rejected_duplicate_leaves_the_original_untouched(self):
        pipeline = RequestPipeline()
        pipeline.submit_request({**ESCALATING, "request_id": "req-dup-002"})
        original = pipeline.list_pending_notifications()[0]

        pipeline.submit_request({**ESCALATING, "request_id": "req-dup-002"})

        assert pipeline.list_pending_notifications() == [original]
        assert pipeline.list_pending_notifications()[0] is original
        assert pipeline.resolve_escalation("req-dup-002", human_decision="approved").status == "granted"

    def test_different_ids_do_not_conflict(self):
        pipeline = RequestPipeline()

        first = pipeline.submit_request({**ESCALATING, "request_id": "req-dup-003"})
        second = pipeline.submit_request({**ESCALATING, "request_id": "req-dup-004"})

        assert first.status == second.status == "pending_approval"


class TestPendingReadAccess:

    def test_has_pending_request_follows_the_lifecycle(self):
        pipeline = RequestPipeline()
        assert pipeline.has_pending_request("req-pend-001") is False

        pipeline.submit_request({**ESCALATING, "request_id": "req-pend-001"})
        assert pipeline.has_pending_request("req-pend-001") is True

        pipeline.resolve_escalation("req-pend-001", human_decision="rejected")
        assert pipeline.has_pending_request("req-pend-001") is False

    def test_list_pending_notifications_in_submission_order(self):
        pipeline = RequestPipeline()
        assert pipeline.list_pending_notifications() == []

        pipeline.submit_request({**ESCALATING, "request_id": "req-pend-002"})
        pipeline.submit_request({**ESCALATING, "request_id": "req-pend-003"})
        assert [n.request_id for n in pipeline.list_pending_notifications()] == ["req-pend-002", "req-pend-003"]

        pipeline.resolve_escalation("req-pend-002", human_decision="approved")
        assert [n.request_id for n in pipeline.list_pending_notifications()] == ["req-pend-003"]


class TestAuditRecordStore:

    def test_direct_outcome_stores_one_record(self):
        pipeline = RequestPipeline()

        result = pipeline.submit_request({**GRANTED, "request_id": "req-aud-001"})

        assert pipeline.audit_records == [result.audit_record]

    def test_pending_escalation_stores_nothing_until_resolved(self):
        pipeline = RequestPipeline()
        pipeline.submit_request({**ESCALATING, "request_id": "req-aud-002"})
        assert pipeline.audit_records == []

        resolved = pipeline.resolve_escalation("req-aud-002", human_decision="approved")

        assert pipeline.audit_records == [resolved.audit_record]
        assert resolved.audit_record.final_decision == "GRANTED"

    def test_errors_and_clarifications_store_nothing(self):
        pipeline = RequestPipeline()
        pipeline.submit_request({**GRANTED, "session_expired": True})
        pipeline.submit_request({"user_id": "user-001", "resource_name": "e", "session_token": "abc"})

        assert pipeline.audit_records == []

    def test_safe_mode_block_then_recovery_stores_both_records(self):
        pipeline = RequestPipeline()
        pipeline.submit_request({**ESCALATING, "request_id": "req-aud-003"})
        pipeline.recovery_fsm.state = SystemState.SAFE_MODE_ACTIVE
        pipeline.resolve_escalation("req-aud-003", human_decision="approved")

        pipeline.recovery_fsm.state = SystemState.SYSTEM_NORMAL
        pipeline.resolve_escalation("req-aud-003", human_decision="approved")

        assert [r.final_decision for r in pipeline.audit_records] == ["PENDING", "GRANTED"]
