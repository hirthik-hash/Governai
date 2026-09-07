# backend/tests/test_request_agent.py

from agents.request_agent import RequestUnderstandingAgent


class TestRequestUnderstandingAgentValidInput:

    def test_known_user_and_resource_succeeds(self):
        agent = RequestUnderstandingAgent()
        result = agent.process({"user_id": "user-001", "resource_id": "resource-001"})

        assert result.success is True

    def test_output_includes_correct_clearance_fields(self):
        agent = RequestUnderstandingAgent()
        # user-008: Rina Sato, clearance 0; resource-005: Salary Records, requires 5
        result = agent.process({"user_id": "user-008", "resource_id": "resource-005"})

        assert result.data["clearance"] == 0
        assert result.data["required_clearance"] == 5

    def test_blacklisted_user_is_flagged_in_output(self):
        agent = RequestUnderstandingAgent()
        result = agent.process({"user_id": "user-009", "resource_id": "resource-001"})

        assert result.data["blacklist_match"] is True

    def test_cross_department_request_is_detected(self):
        agent = RequestUnderstandingAgent()
        # user-001 is engineering, resource-003 is finance
        result = agent.process({"user_id": "user-001", "resource_id": "resource-003"})

        assert result.data["cross_department_request"] is True
        assert result.data["requester_department"] == "engineering"
        assert result.data["resource_department"] == "finance"

    def test_same_department_request_is_not_flagged(self):
        agent = RequestUnderstandingAgent()
        # user-001 and resource-002 are both engineering
        result = agent.process({"user_id": "user-001", "resource_id": "resource-002"})

        assert result.data["cross_department_request"] is False

    def test_ambiguity_flag_defaults_to_false(self):
        agent = RequestUnderstandingAgent()
        result = agent.process({"user_id": "user-001", "resource_id": "resource-001"})

        assert result.data["ambiguity_flag"] is False

    def test_reasoning_mentions_cross_department_when_applicable(self):
        agent = RequestUnderstandingAgent()
        result = agent.process({"user_id": "user-001", "resource_id": "resource-003"})

        assert "cross-department" in result.reasoning.lower()


class TestRequestUnderstandingAgentInvalidInput:

    def test_missing_user_id_fails_gracefully(self):
        agent = RequestUnderstandingAgent()
        result = agent.process({"resource_id": "resource-001"})

        assert result.success is False
        assert len(result.errors) > 0

    def test_missing_resource_id_fails_gracefully(self):
        agent = RequestUnderstandingAgent()
        result = agent.process({"user_id": "user-001"})

        assert result.success is False

    def test_unknown_user_id_fails_gracefully_not_raises(self):
        agent = RequestUnderstandingAgent()
        result = agent.process({"user_id": "user-999", "resource_id": "resource-001"})

        assert result.success is False
        assert "user-999" in result.reasoning

    def test_unknown_resource_id_fails_gracefully_not_raises(self):
        agent = RequestUnderstandingAgent()
        result = agent.process({"user_id": "user-001", "resource_id": "resource-999"})

        assert result.success is False
        assert "resource-999" in result.reasoning


class TestRequestAgentOutputFeedsFsm:

    def test_agent_output_merges_cleanly_into_fsm_context(self):
        from fsm.governance_fsm import GovernanceFSM
        from fsm.states import RequestState

        agent = RequestUnderstandingAgent()
        result = agent.process({"user_id": "user-007", "resource_id": "resource-001"})

        context = result.merge_into_context({"risk_score": 10})
        fsm = GovernanceFSM(request_id="agent-test-001")
        final_state = fsm.run_until_stuck(context)

        assert final_state == RequestState.CLOSED