from datetime import datetime, timezone
from core.request_history_tracker import RequestHistoryTracker
from agents.security_agent import (
    SecurityRiskAgent,
    FACTOR_WEIGHTS,
    MAX_POSSIBLE_RISK_WEIGHT,
)
from core.geo_anomaly_detector import GeoAnomalyDetector
from agents.security_agent import compute_risk_level_and_recommendation

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


class TestSecurityRiskAgentGeographicAnomaly:

    def test_anomalous_location_triggers_factor(self):
        geo = GeoAnomalyDetector()
        geo.record_location("user-001", "US")

        agent = SecurityRiskAgent(geo_detector=geo)
        result = agent.process({
            "user_id": "user-001", "clearance": 5, "required_clearance": 2,
            "location": "RU",
        })

        assert "geographic_anomaly" in result.data["risk_triggers"]

    def test_known_location_does_not_trigger(self):
        geo = GeoAnomalyDetector()
        geo.record_location("user-001", "US")

        agent = SecurityRiskAgent(geo_detector=geo)
        result = agent.process({
            "user_id": "user-001", "clearance": 5, "required_clearance": 2,
            "location": "US",
        })

        assert "geographic_anomaly" not in result.data["risk_triggers"]

    def test_first_ever_location_does_not_trigger(self):
        agent = SecurityRiskAgent()
        result = agent.process({
            "user_id": "user-001", "clearance": 5, "required_clearance": 2,
            "location": "US",
        })

        assert "geographic_anomaly" not in result.data["risk_triggers"]

    def test_missing_location_skips_check_without_crashing(self):
        agent = SecurityRiskAgent()
        result = agent.process({
            "user_id": "user-001", "clearance": 5, "required_clearance": 2,
        })

        assert result.success is True
        assert "geographic_anomaly" not in result.data["risk_triggers"]


class TestSecurityRiskAgentAllSixFactorsCombined:

    def test_all_six_factors_reaches_hard_denial_territory(self):
        """
        With every factor now implemented, maxing all 6 out should
        finally clear the FSM's hard-denial threshold of 85 - Day 37
        showed 5 factors alone (raw 87/112 ≈ 78) could not.
        """
        history = RequestHistoryTracker(failure_threshold=3, rapid_threshold=5)
        for _ in range(3):
            history.record_request("user-009", was_denied=True)
        for _ in range(2):
            history.record_request("user-009", was_denied=False)

        geo = GeoAnomalyDetector()
        geo.record_location("user-009", "US")

        agent = SecurityRiskAgent(history_tracker=history, geo_detector=geo)
        result = agent.process({
            "user_id": "user-009",
            "clearance": 0, "required_clearance": 5,
            "cross_department_request": True,
            "after_hours_access": True,
            "location": "RU",  # anomalous vs. known "US"
        })

        assert set(result.data["risk_triggers"]) == set(FACTOR_WEIGHTS.keys())
        assert result.data["risk_score"] == 100
        assert result.data["risk_score"] >= 85

    def test_all_six_factors_feeds_fsm_to_hard_denial(self):
        from fsm.governance_fsm import GovernanceFSM
        from fsm.states import RequestState

        history = RequestHistoryTracker(failure_threshold=3, rapid_threshold=5)
        for _ in range(3):
            history.record_request("user-009", was_denied=True)
        for _ in range(2):
            history.record_request("user-009", was_denied=False)

        geo = GeoAnomalyDetector()
        geo.record_location("user-009", "US")

        agent = SecurityRiskAgent(history_tracker=history, geo_detector=geo)
        result = agent.process({
            "user_id": "user-009",
            "clearance": 0, "required_clearance": 5,
            "cross_department_request": True,
            "after_hours_access": True,
            "location": "RU",
        })

        context = {
            "ambiguity_flag": False,
            "clearance": 0,
            "required_clearance": 5,
            "risk_score": result.data["risk_score"],
        }

        fsm = GovernanceFSM(request_id="security-integration-002")
        fsm.transition(context)
        fsm.transition(context)
        fsm.transition(context)
        new_state = fsm.transition(context)

        assert new_state == RequestState.HARD_DENIED


class TestRiskLevelAndRecommendationThresholds:

    def test_zero_risk_is_low_allow(self):
        level, rec = compute_risk_level_and_recommendation(0)
        assert level == "LOW"
        assert rec == "ALLOW"

    def test_just_under_escalation_threshold_is_medium_monitor(self):
        # escalation threshold is 40, half of that is 20
        level, rec = compute_risk_level_and_recommendation(39)
        assert level == "MEDIUM"
        assert rec == "MONITOR"

    def test_at_escalation_threshold_is_high_escalate(self):
        level, rec = compute_risk_level_and_recommendation(40)
        assert level == "HIGH"
        assert rec == "ESCALATE"

    def test_just_under_hard_denial_threshold_is_high_escalate(self):
        level, rec = compute_risk_level_and_recommendation(84)
        assert level == "HIGH"
        assert rec == "ESCALATE"

    def test_at_hard_denial_threshold_is_critical_hard_deny(self):
        level, rec = compute_risk_level_and_recommendation(85)
        assert level == "CRITICAL"
        assert rec == "HARD_DENY"

    def test_maximum_score_is_critical_hard_deny(self):
        level, rec = compute_risk_level_and_recommendation(100)
        assert level == "CRITICAL"
        assert rec == "HARD_DENY"

    def test_low_medium_boundary_at_half_escalation_threshold(self):
        # half of 40 is 20
        low_side_level, _ = compute_risk_level_and_recommendation(19)
        medium_side_level, _ = compute_risk_level_and_recommendation(20)

        assert low_side_level == "LOW"
        assert medium_side_level == "MEDIUM"


class TestSecurityRiskAgentIncludesLevelAndRecommendation:

    def test_output_includes_risk_level_and_recommendation(self):
        agent = SecurityRiskAgent()
        result = agent.process({"clearance": 5, "required_clearance": 2})

        assert "risk_level" in result.data
        assert "recommendation" in result.data
        assert result.data["risk_level"] == "LOW"
        assert result.data["recommendation"] == "ALLOW"

    def test_reasoning_mentions_recommendation(self):
        agent = SecurityRiskAgent()
        result = agent.process({
            "clearance": 0, "required_clearance": 5,
            "cross_department_request": True,
            "after_hours_access": True,
        })

        assert "recommendation" in result.reasoning.lower()

    def test_thresholds_used_by_agent_match_fsm_thresholds(self):
        """
        Locks in the Day 39 design goal directly: whatever risk_score
        this agent computes, its recommendation must never contradict
        what the FSM itself would do with that exact score. This test
        walks the FSM's own escalation/hard-denial boundary values
        (40 and 85, from fsm/transitions.py) through the agent's
        recommendation function and confirms agreement.
        """
        from fsm.transitions import needs_escalation, is_hard_denied

        for score in [0, 10, 39, 40, 41, 84, 85, 86, 100]:
            level, recommendation = compute_risk_level_and_recommendation(score)

            fsm_ctx = {"risk_score": score, "clearance": 0, "required_clearance": 0}
            fsm_hard_denies = is_hard_denied(fsm_ctx)
            fsm_escalates = needs_escalation(fsm_ctx)

            if fsm_hard_denies:
                assert recommendation == "HARD_DENY", f"Mismatch at score={score}"
            elif fsm_escalates:
                assert recommendation == "ESCALATE", f"Mismatch at score={score}"