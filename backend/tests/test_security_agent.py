# backend/tests/test_security_agent.py

from agents.security_agent import (
    SecurityRiskIntelligenceAgent,
    classify_risk_level,
    FACTOR_WEIGHTS,
    MAX_POSSIBLE_SCORE,
)


class TestRiskLevelClassification:

    def test_zero_is_low(self):
        assert classify_risk_level(0) == "LOW"

    def test_thirty_nine_is_low(self):
        assert classify_risk_level(39) == "LOW"

    def test_forty_is_medium(self):
        assert classify_risk_level(40) == "MEDIUM"

    def test_eighty_four_is_medium(self):
        assert classify_risk_level(84) == "MEDIUM"

    def test_eighty_five_is_high(self):
        assert classify_risk_level(85) == "HIGH"

    def test_hundred_is_high(self):
        assert classify_risk_level(100) == "HIGH"


class TestFactorWeightsAreStable:

    def test_all_six_factors_defined(self):
        expected = {
            "repeated_failures", "unusual_hour", "department_mismatch",
            "rapid_succession", "geographic_anomaly", "classification_jump",
        }
        assert set(FACTOR_WEIGHTS.keys()) == expected

    def test_max_possible_score_is_sum_of_all_weights(self):
        assert MAX_POSSIBLE_SCORE == sum(FACTOR_WEIGHTS.values())


class TestSecurityRiskAgentNoFactorsTriggered:

    def test_no_factors_gives_zero_risk(self):
        agent = SecurityRiskIntelligenceAgent()
        result = agent.process({
            "clearance": 3, "required_clearance": 2,
            "cross_department_request": False,
            "after_hours_access": False,
        })

        assert result.data["risk_score"] == 0
        assert result.data["risk_level"] == "LOW"
        assert result.data["triggered_factors"] == []


class TestSecurityRiskAgentDepartmentMismatch:

    def test_department_mismatch_alone_triggers_correct_score(self):
        agent = SecurityRiskIntelligenceAgent()
        result = agent.process({
            "clearance": 3, "required_clearance": 2,
            "cross_department_request": True,
            "after_hours_access": False,
        })

        expected_score = round((15 / MAX_POSSIBLE_SCORE) * 100)
        assert result.data["risk_score"] == expected_score
        assert "department_mismatch" in result.data["triggered_factors"]


class TestSecurityRiskAgentUnusualHour:

    def test_after_hours_alone_triggers_correct_score(self):
        agent = SecurityRiskIntelligenceAgent()
        result = agent.process({
            "clearance": 3, "required_clearance": 2,
            "cross_department_request": False,
            "after_hours_access": True,
        })

        expected_score = round((20 / MAX_POSSIBLE_SCORE) * 100)
        assert result.data["risk_score"] == expected_score
        assert "unusual_hour" in result.data["triggered_factors"]


class TestSecurityRiskAgentClassificationJump:

    def test_gap_of_two_triggers_classification_jump(self):
        agent = SecurityRiskIntelligenceAgent()
        result = agent.process({
            "clearance": 1, "required_clearance": 3,  # gap of 2
            "cross_department_request": False,
            "after_hours_access": False,
        })

        assert "classification_jump" in result.data["triggered_factors"]

    def test_gap_of_one_does_not_trigger_classification_jump(self):
        agent = SecurityRiskIntelligenceAgent()
        result = agent.process({
            "clearance": 2, "required_clearance": 3,  # gap of 1
            "cross_department_request": False,
            "after_hours_access": False,
        })

        assert "classification_jump" not in result.data["triggered_factors"]

    def test_large_gap_still_only_counts_once(self):
        agent = SecurityRiskIntelligenceAgent()
        result = agent.process({
            "clearance": 0, "required_clearance": 5,  # gap of 5
            "cross_department_request": False,
            "after_hours_access": False,
        })

        expected_score = round((30 / MAX_POSSIBLE_SCORE) * 100)
        assert result.data["risk_score"] == expected_score


class TestSecurityRiskAgentMultipleFactors:

    def test_all_three_implemented_factors_combine_correctly(self):
        agent = SecurityRiskIntelligenceAgent()
        result = agent.process({
            "clearance": 0, "required_clearance": 5,  # classification_jump
            "cross_department_request": True,          # department_mismatch
            "after_hours_access": True,                 # unusual_hour
        })

        expected_weight = 30 + 15 + 20
        expected_score = round((expected_weight / MAX_POSSIBLE_SCORE) * 100)
        assert result.data["risk_score"] == expected_score
        assert len(result.data["triggered_factors"]) == 3

    def test_not_yet_implemented_factors_never_trigger(self):
        """
        Locks in today's honest limitation: these three factors are
        defined but always False until Days 37-39 build real
        detection logic for them.
        """
        agent = SecurityRiskIntelligenceAgent()
        result = agent.process({
            "clearance": 0, "required_clearance": 5,
            "cross_department_request": True,
            "after_hours_access": True,
        })

        for stub_factor in ("repeated_failures", "rapid_succession", "geographic_anomaly"):
            assert stub_factor not in result.data["triggered_factors"]


class TestSecurityRiskAgentInvalidInput:

    def test_missing_clearance_fails_gracefully(self):
        agent = SecurityRiskIntelligenceAgent()
        result = agent.process({"cross_department_request": True})

        assert result.success is False


class TestSecurityRiskAgentIntegratesWithEarlierAgents:

    def test_full_chain_through_three_agents_into_fsm(self):
        from agents.request_agent import RequestUnderstandingAgent
        from agents.validation_agent import AccessValidationAgent
        from fsm.governance_fsm import GovernanceFSM
        from fsm.states import RequestState

        request_agent = RequestUnderstandingAgent()
        request_result = request_agent.process({
            "user_id": "user-001",  # engineering, clearance 1
            "resource_id": "resource-003",  # finance, Q4 Financial Report, requires 3
        })

        validation_agent = AccessValidationAgent()
        combined = dict(request_result.data)
        combined["session_token"] = "abc123"
        validation_result = validation_agent.process(combined)

        risk_agent = SecurityRiskIntelligenceAgent()
        combined.update(validation_result.data)
        risk_result = risk_agent.process(combined)

        assert risk_result.success is True
        # cross-department (engineering -> finance) should be flagged
        assert "department_mismatch" in risk_result.data["triggered_factors"]

        final_context = dict(combined)
        final_context.update(risk_result.data)
        final_context["escalation_sent"] = True
        final_context["approval_token_valid"] = True

        fsm = GovernanceFSM(request_id="security-integration-001")
        final_state = fsm.run_until_stuck(final_context)

        # clearance 1 < required 3, so this should need escalation,
        # not straight authorization
        assert final_state in (RequestState.CLOSED, RequestState.ACCESS_GRANTED)