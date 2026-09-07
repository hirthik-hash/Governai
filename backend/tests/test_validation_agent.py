# backend/tests/test_validation_agent.py

from agents.validation_agent import AccessValidationAgent


class TestAccessValidationAgentBasicClearance:

    def test_sufficient_clearance_passes(self):
        agent = AccessValidationAgent()
        result = agent.process({"clearance": 3, "required_clearance": 2})

        assert result.success is True
        assert result.data["clearance_sufficient"] is True
        assert result.data["role_override_applied"] is False

    def test_insufficient_clearance_without_role_fails_check(self):
        agent = AccessValidationAgent()
        result = agent.process({"clearance": 1, "required_clearance": 3})

        assert result.success is True  # the agent itself succeeded at reasoning
        assert result.data["clearance_sufficient"] is False

    def test_exact_clearance_match_passes(self):
        agent = AccessValidationAgent()
        result = agent.process({"clearance": 2, "required_clearance": 2})

        assert result.data["clearance_sufficient"] is True

    def test_missing_clearance_fields_fails_gracefully(self):
        agent = AccessValidationAgent()
        result = agent.process({"role": "CISO"})

        assert result.success is False
        assert len(result.errors) > 0


class TestAccessValidationAgentRoleOverride:

    def test_ciso_override_applies_for_security_resource(self):
        agent = AccessValidationAgent()
        result = agent.process({
            "clearance": 1,
            "required_clearance": 5,
            "role": "CISO",
            "resource_sensitivity": "security",
        })

        assert result.data["clearance_sufficient"] is True
        assert result.data["role_override_applied"] is True

    def test_ciso_override_applies_for_top_secret_resource(self):
        agent = AccessValidationAgent()
        result = agent.process({
            "clearance": 0,
            "required_clearance": 5,
            "role": "CISO",
            "resource_sensitivity": "top_secret",
        })

        assert result.data["role_override_applied"] is True

    def test_ciso_override_does_not_apply_for_unrelated_sensitivity(self):
        agent = AccessValidationAgent()
        result = agent.process({
            "clearance": 1,
            "required_clearance": 5,
            "role": "CISO",
            "resource_sensitivity": "internal",
        })

        assert result.data["clearance_sufficient"] is False
        assert result.data["role_override_applied"] is False

    def test_override_never_applies_when_role_not_in_table(self):
        agent = AccessValidationAgent()
        result = agent.process({
            "clearance": 1,
            "required_clearance": 5,
            "role": "Software Engineer",
            "resource_sensitivity": "top_secret",
        })

        assert result.data["role_override_applied"] is False

    def test_override_never_needed_when_clearance_already_sufficient(self):
        """
        A CISO with genuinely sufficient clearance shouldn't show
        role_override_applied=True - the override only exists to
        explain shortfalls, not to relabel a normal pass.
        """
        agent = AccessValidationAgent()
        result = agent.process({
            "clearance": 5,
            "required_clearance": 2,
            "role": "CISO",
            "resource_sensitivity": "top_secret",
        })

        assert result.data["clearance_sufficient"] is True
        assert result.data["role_override_applied"] is False


class TestAccessValidationAgentIntegratesWithRequestAgent:

    def test_chains_cleanly_after_request_understanding_agent(self):
        from agents.request_agent import RequestUnderstandingAgent

        request_agent = RequestUnderstandingAgent()
        request_result = request_agent.process({
            "user_id": "user-008",  # Rina Sato, clearance 0
            "resource_id": "resource-005",  # Salary Records, requires 5
        })

        validation_agent = AccessValidationAgent()
        validation_result = validation_agent.process(request_result.data)

        assert validation_result.success is True
        assert validation_result.data["clearance_sufficient"] is False

    def test_combined_output_still_feeds_fsm_correctly(self):
        from agents.request_agent import RequestUnderstandingAgent
        from fsm.governance_fsm import GovernanceFSM
        from fsm.states import RequestState

        request_agent = RequestUnderstandingAgent()
        request_result = request_agent.process({
            "user_id": "user-007",  # CISO, clearance 5
            "resource_id": "resource-001",  # public, requires 0
        })

        validation_agent = AccessValidationAgent()
        validation_result = validation_agent.process(request_result.data)

        context = request_result.data.copy()
        context.update(validation_result.data)
        context["risk_score"] = 10

        fsm = GovernanceFSM(request_id="validation-integration-001")
        final_state = fsm.run_until_stuck(context)

        assert final_state == RequestState.CLOSED