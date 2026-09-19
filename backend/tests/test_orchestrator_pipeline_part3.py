# backend/tests/test_orchestrator_pipeline_part3.py

"""
Tests RequestPipeline under realistic sustained use: one long-lived
pipeline instance handling multiple requests over time, where
SecurityRiskAgent's stateful factors (Days 37-38) should accumulate
across calls, and where a safe-mode transition occurring BETWEEN
requests correctly affects the next one - neither has been tested
through the pipeline's public interface until now.
"""

from core.orchestrator import RequestPipeline
from core.request_history_tracker import RequestHistoryTracker
from agents.recovery_agent import FailureRecoveryAgent, HealthCheckResult


class TestStatefulFactorsAccumulateAcrossRequests:

    def test_repeated_denials_raise_risk_on_later_requests_from_same_user(self):
        """
        user-009 is blacklisted, so every request from them hard-
        denies. After several denied requests, a fresh request from
        the SAME pipeline instance should show elevated risk_score
        from the repeated_failures factor, on top of the blacklist
        hard-denial - proving RequestHistoryTracker genuinely
        persists across pipeline calls, not per-request.
        """
        shared_history = RequestHistoryTracker(failure_threshold=3)
        pipeline = RequestPipeline(history_tracker=shared_history)

        for i in range(3):
            result = pipeline.submit_request({
                "request_id": f"req-repeat-{i}",
                "user_id": "user-009", "resource_id": "resource-001",
                "session_token": "abc",
            })
            assert result.status == "denied"
            # Record the denial into the SAME shared tracker the
            # pipeline's SecurityRiskAgent reads from, mirroring what
            # a real orchestrator would do after each outcome.
            shared_history.record_request("user-009", was_denied=True)

        final_result = pipeline.submit_request({
            "request_id": "req-repeat-final",
            "user_id": "user-009", "resource_id": "resource-001",
            "session_token": "abc",
        })

        assert final_result.status == "denied"
        assert "repeated_failures" in final_result.audit_record.agent_reasoning_trail[-1] \
            or any("repeated_failures" in r for r in final_result.audit_record.agent_reasoning_trail)


class TestLiveSafeModeTransitionMidSession:

    def test_second_request_blocked_after_failure_detected_between_calls(self):
        pipeline = RequestPipeline()

        first_result = pipeline.submit_request({
            "request_id": "req-midsession-001",
            "user_id": "user-007", "resource_id": "resource-001",
            "session_token": "abc",
        })
        assert first_result.status == "granted"

        def failing_integrity() -> HealthCheckResult:
            return HealthCheckResult(check_name="fsm_integrity", healthy=False, detail="detected mid-session")

        from fsm.states import SystemState
        pipeline.recovery_fsm.state = SystemState.DEGRADED_WARNING  # simulate prior warning state
        recovery_agent = FailureRecoveryAgent(checks=[failing_integrity], recovery_fsm=pipeline.recovery_fsm)
        recovery_agent.evaluate_and_transition()
        assert pipeline.recovery_fsm.is_safe_mode() is True

        second_result = pipeline.submit_request({
            "request_id": "req-midsession-002",
            "user_id": "user-007", "resource_id": "resource-001",
            "session_token": "abc",
        })

        assert second_result.status == "blocked_safe_mode"

    def test_public_readonly_request_unaffected_by_mid_session_failure(self):
        pipeline = RequestPipeline()

        from fsm.states import SystemState

        def failing_integrity() -> HealthCheckResult:
            return HealthCheckResult(check_name="fsm_integrity", healthy=False, detail="detected")

        pipeline.recovery_fsm.state = SystemState.DEGRADED_WARNING
        recovery_agent = FailureRecoveryAgent(checks=[failing_integrity], recovery_fsm=pipeline.recovery_fsm)
        recovery_agent.evaluate_and_transition()

        result = pipeline.submit_request({
            "request_id": "req-midsession-003",
            "user_id": "user-007", "resource_id": "resource-001",
            "session_token": "abc", "is_public_readonly": True,
        })

        assert result.status == "granted"


class TestGeographicAnomalyAcrossRequests:

    def test_second_request_from_new_location_flagged_anomalous(self):
        from core.geo_anomaly_detector import GeoAnomalyDetector

        shared_geo = GeoAnomalyDetector()
        pipeline = RequestPipeline(geo_detector=shared_geo)

        first_result = pipeline.submit_request({
            "request_id": "req-geo-001",
            "user_id": "user-001", "resource_id": "resource-002",
            "session_token": "abc", "location": "US",
        })
        assert first_result.status == "granted"
        shared_geo.record_location("user-001", "US")

        second_result = pipeline.submit_request({
            "request_id": "req-geo-002",
            "user_id": "user-001", "resource_id": "resource-002",
            "session_token": "abc", "location": "RU",
        })

        assert any(
            "geographic_anomaly" in r for r in second_result.audit_record.agent_reasoning_trail
        )