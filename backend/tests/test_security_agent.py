from datetime import datetime, timezone
from core.request_history_tracker import RequestHistoryTracker
from agents.security_agent import (
    SecurityRiskAgent,
    FACTOR_WEIGHTS,
    MAX_POSSIBLE_RISK_WEIGHT,
)

class TestSecurityRiskAgentRepeatedFailures:

    def test_repeated_failures_trigger_factor(self):
        tracker = RequestHistoryTracker(failure_threshold=3)
        tracker.record_request("user-009", was_denied=True)
        tracker.record_request("user-009", was_denied=True)
        tracker.record_request("user-009", was_denied=True)

        agent = SecurityRiskAgent(history_tracker=tracker)
        result = agent.process({
            "user_id": "user-009", "clearance": 5, "required_clearance": 2,
        })

        assert "repeated_failures" in result.data["risk_triggers"]

    def test_no_history_means_factor_not_triggered(self):
        agent = SecurityRiskAgent()
        result = agent.process({
            "user_id": "user-001", "clearance": 5, "required_clearance": 2,
        })

        assert "repeated_failures" not in result.data["risk_triggers"]

    def test_missing_user_id_skips_history_check_without_crashing(self):
        agent = SecurityRiskAgent()
        result = agent.process({"clearance": 5, "required_clearance": 2})

        assert result.success is True
        assert "repeated_failures" not in result.data["risk_triggers"]


class TestSecurityRiskAgentRapidSuccession:

    def test_rapid_requests_trigger_factor(self):
        tracker = RequestHistoryTracker(rapid_threshold=5)
        for _ in range(5):
            tracker.record_request("user-001")

        agent = SecurityRiskAgent(history_tracker=tracker)
        result = agent.process({
            "user_id": "user-001", "clearance": 5, "required_clearance": 2,
        })

        assert "rapid_succession" in result.data["risk_triggers"]

    def test_normal_pace_does_not_trigger(self):
        agent = SecurityRiskAgent()
        result = agent.process({
            "user_id": "user-001", "clearance": 5, "required_clearance": 2,
        })

        assert "rapid_succession" not in result.data["risk_triggers"]


class TestSecurityRiskAgentAllFiveFactorsCombined:

    def test_all_five_currently_implemented_factors_together(self):
        tracker = RequestHistoryTracker(failure_threshold=3, rapid_threshold=5)
        for _ in range(3):
            tracker.record_request("user-009", was_denied=True)
        for _ in range(5):
            tracker.record_request("user-009", was_denied=False)

        agent = SecurityRiskAgent(history_tracker=tracker)
        result = agent.process({
            "user_id": "user-009",
            "clearance": 0, "required_clearance": 5,
            "cross_department_request": True,
            "after_hours_access": True,
        })

        expected_factors = {
            "classification_jump", "department_mismatch", "unusual_hour",
            "repeated_failures", "rapid_succession",
        }
        assert set(result.data["risk_triggers"]) == expected_factors

        expected_raw = sum(FACTOR_WEIGHTS[f] for f in expected_factors)
        expected_score = round((expected_raw / MAX_POSSIBLE_RISK_WEIGHT) * 100)
        assert result.data["risk_score"] == expected_score

    def test_max_out_of_five_factors_still_under_hard_denial_threshold(self):
        """
        Documents current behavior: even with all 5 implemented
        factors triggered (raw weight 87/112 = 78), the score stays
        below the FSM's hard-denial threshold of 85 - only
        geographic_anomaly (Day 38) can push a request that high on
        behavioral factors alone.
        """
        tracker = RequestHistoryTracker(failure_threshold=3, rapid_threshold=5)
        for _ in range(3):
            tracker.record_request("user-009", was_denied=True)
        for _ in range(2):
            tracker.record_request("user-009", was_denied=False)

        agent = SecurityRiskAgent(history_tracker=tracker)
        result = agent.process({
            "user_id": "user-009",
            "clearance": 0, "required_clearance": 5,
            "cross_department_request": True,
            "after_hours_access": True,
        })

        assert result.data["risk_score"] < 85