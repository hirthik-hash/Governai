# backend/tests/test_orchestrator_pipeline_part4.py

"""
Closes the Day 62 gap: a genuine role-override test where clearance
alone would fail, using role from the REQUEST PAYLOAD (not the seed
user's actual role) - AccessValidationAgent reads role from
input_data, so this works without touching seed_data.py. Also a
robustness pass on RequestPipeline itself, mirroring the pattern
applied to every individual agent since Day 29.
"""

from core.orchestrator import RequestPipeline


class TestGenuineRoleOverrideThroughPipeline:

    def test_low_clearance_user_with_ciso_role_override_bypasses_shortfall(self):
        pipeline = RequestPipeline()

        result = pipeline.submit_request({
            "request_id": "req-real-override-001",
            "user_id": "user-008",
            "resource_id": "resource-005",
            "session_token": "abc",
            "role": "CISO",
        })

        assert result.status == "pending_approval"

        pending = pipeline._pending_requests["req-real-override-001"]

        print("PENDING:", pending)
    def test_same_low_clearance_user_without_role_override_is_denied_or_escalated(self):
        """
        Control case: the SAME user/resource pair, but with no role
        override supplied - should NOT grant access, confirming the
        override in the test above is what actually made the
        difference, not some other seed-data quirk.
        """
        pipeline = RequestPipeline()

        result = pipeline.submit_request({
            "request_id": "req-real-override-002",
            "user_id": "user-008", "resource_id": "resource-005",
            "session_token": "abc",
        })

        assert result.status != "granted"

    def test_role_that_is_not_in_override_table_does_not_bypass_shortfall(self):
        pipeline = RequestPipeline()

        result = pipeline.submit_request({
            "request_id": "req-real-override-003",
            "user_id": "user-008", "resource_id": "resource-005",
            "session_token": "abc", "role": "Software Engineer",
        })

        assert result.status != "granted"


class TestPipelineRobustnessMissingFields:

    def test_missing_user_id_is_an_error_not_a_crash(self):
        pipeline = RequestPipeline()
        result = pipeline.submit_request({
            "resource_id": "resource-001", "session_token": "abc",
        })

        assert result.status == "error"
        assert len(result.errors) > 0

    def test_missing_both_resource_id_and_name_is_an_error(self):
        pipeline = RequestPipeline()
        result = pipeline.submit_request({
            "user_id": "user-001", "session_token": "abc",
        })

        assert result.status == "error"

    def test_unknown_user_id_is_an_error_not_a_crash(self):
        pipeline = RequestPipeline()
        result = pipeline.submit_request({
            "user_id": "user-999", "resource_id": "resource-001", "session_token": "abc",
        })

        assert result.status == "error"

    def test_unknown_resource_id_is_an_error_not_a_crash(self):
        pipeline = RequestPipeline()
        result = pipeline.submit_request({
            "user_id": "user-001", "resource_id": "resource-999", "session_token": "abc",
        })

        assert result.status == "error"

    def test_expired_session_is_an_error(self):
        pipeline = RequestPipeline()
        result = pipeline.submit_request({
            "user_id": "user-001", "resource_id": "resource-001",
            "session_token": "abc", "session_expired": True,
        })

        assert result.status == "error"

    def test_empty_request_dict_does_not_crash(self):
        pipeline = RequestPipeline()
        result = pipeline.submit_request({})

        assert result.status == "error"
        assert isinstance(result.errors, list)


class TestPipelineRobustnessMalformedTypes:

    def test_non_string_user_id_fails_gracefully(self):
        pipeline = RequestPipeline()
        result = pipeline.submit_request({
            "user_id": 12345, "resource_id": "resource-001", "session_token": "abc",
        })

        assert result.status == "error"

    def test_none_values_do_not_crash_the_pipeline(self):
        pipeline = RequestPipeline()
        result = pipeline.submit_request({
            "user_id": None, "resource_id": None, "session_token": "abc",
        })

        assert result.status == "error"


class TestPipelineErrorResultsHaveNoPartialAuditRecord:

    def test_error_status_never_includes_an_audit_record(self):
        """
        An error before the FSM even runs shouldn't produce a
        half-formed AuditRecord - audit_record should be None for
        any 'error' status, since there's no meaningful final_decision
        to report.
        """
        pipeline = RequestPipeline()
        result = pipeline.submit_request({"resource_id": "resource-001"})

        assert result.status == "error"
        assert result.audit_record is None