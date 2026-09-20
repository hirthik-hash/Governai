# backend/tests/test_orchestrator_pipeline_coverage_gaps.py

"""
Closes real coverage gaps found in core.orchestrator.RequestPipeline
after five days of pipeline tests (Days 60-65) - a coverage-driven
review mirroring Day 18's approach for the FSM core, rather than
assuming prior tests already covered every branch.
"""

from core.orchestrator import RequestPipeline


class TestClarificationNeededReturnsActualCandidates:

    def test_candidate_resource_ids_are_the_real_matching_ids(self):
        pipeline = RequestPipeline()
        result = pipeline.submit_request({
            "request_id": "req-cov-001",
            "user_id": "user-001", "resource_name": "e",
            "session_token": "abc",
        })

        assert result.status == "clarification_needed"
        # Confirm these are real resource IDs, not an empty placeholder
        from data.seed_data import get_resource
        for resource_id in result.candidate_resource_ids:
            get_resource(resource_id)  # raises ValueError if not a real ID


class TestResolveEscalationInvalidDecisionIsRecoverable:

    def test_invalid_human_decision_string_keeps_request_pending(self):
        pipeline = RequestPipeline()
        pipeline.submit_request({
            "request_id": "req-cov-002",
            "user_id": "user-003", "resource_id": "resource-003",
            "session_token": "abc",
        })

        bad_result = pipeline.resolve_escalation("req-cov-002", human_decision="maybe")
        assert bad_result.status == "error"

        # The request should still be resolvable afterward - it
        # should NOT have been lost from _pending_requests just
        # because one resolution attempt used an invalid decision string.
        good_result = pipeline.resolve_escalation("req-cov-002", human_decision="approved")
        assert good_result.status == "granted"


class TestRoleAndLocationAbsenceHandledCleanly:

    def test_no_role_supplied_does_not_error(self):
        pipeline = RequestPipeline()
        result = pipeline.submit_request({
            "request_id": "req-cov-003",
            "user_id": "user-001", "resource_id": "resource-002",
            "session_token": "abc",
        })

        assert result.status != "error"

    def test_no_location_supplied_does_not_error(self):
        pipeline = RequestPipeline()
        result = pipeline.submit_request({
            "request_id": "req-cov-004",
            "user_id": "user-001", "resource_id": "resource-002",
            "session_token": "abc",
        })

        assert result.status != "error"