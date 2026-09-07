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

# backend/tests/test_request_agent.py — append this class

class TestRequestUnderstandingAgentAmbiguousNameLookup:

    def test_unique_name_match_resolves_immediately(self):
        agent = RequestUnderstandingAgent()
        # "Handbook" only matches "Employee Handbook"
        result = agent.process({"user_id": "user-001", "resource_name": "Handbook"})

        assert result.success is True
        assert result.data["ambiguity_flag"] is False
        assert result.data["resolved_resource_id"] == "resource-001"

    def test_ambiguous_name_sets_ambiguity_flag(self):
        agent = RequestUnderstandingAgent()
        # "Report" or similar broad terms could match multiple - using
        # a query guaranteed to hit 2+ seed resources for this test
        result = agent.process({"user_id": "user-001", "resource_name": "e"})

        # 'e' is deliberately broad - matches many resource names
        assert result.success is True
        assert result.data["ambiguity_flag"] is True
        assert len(result.data["candidate_resource_ids"]) > 1

    def test_no_match_fails_gracefully(self):
        agent = RequestUnderstandingAgent()
        result = agent.process({"user_id": "user-001", "resource_name": "Nonexistent Thing XYZ"})

        assert result.success is False

    def test_resource_id_takes_precedence_over_resource_name(self):
        agent = RequestUnderstandingAgent()
        result = agent.process({
            "user_id": "user-001",
            "resource_id": "resource-001",
            "resource_name": "e",  # would be ambiguous alone, should be ignored
        })

        assert result.data["ambiguity_flag"] is False
        assert result.data["resolved_resource_id"] == "resource-001"

    def test_ambiguous_result_feeds_fsm_into_clarification_state(self):
        from fsm.governance_fsm import GovernanceFSM
        from fsm.states import RequestState

        agent = RequestUnderstandingAgent()
        result = agent.process({"user_id": "user-001", "resource_name": "e"})

        context = result.merge_into_context({"risk_score": 10})
        fsm = GovernanceFSM(request_id="ambiguity-test-001")
        fsm.transition(context)  # -> REQUEST_RECEIVED
        fsm.transition(context)  # -> PARSING_REQUEST
        new_state = fsm.transition(context)  # ambiguous -> CLARIFICATION_REQUESTED

        assert new_state == RequestState.CLARIFICATION_REQUESTED

    def test_missing_both_resource_fields_fails(self):
        agent = RequestUnderstandingAgent()
        result = agent.process({"user_id": "user-001"})

        assert result.success is False
        assert "resource_id or resource_name" in result.errors[0]

# backend/tests/test_request_agent.py — append this class

class TestRequestUnderstandingAgentUrgency:

    def test_urgency_defaults_to_normal_when_omitted(self):
        agent = RequestUnderstandingAgent()
        result = agent.process({"user_id": "user-001", "resource_id": "resource-001"})

        assert result.data["urgency"] == "normal"

    def test_explicit_high_urgency_is_captured(self):
        agent = RequestUnderstandingAgent()
        result = agent.process({
            "user_id": "user-001", "resource_id": "resource-001", "urgency": "high",
        })

        assert result.data["urgency"] == "high"

    def test_invalid_urgency_fails_gracefully(self):
        agent = RequestUnderstandingAgent()
        result = agent.process({
            "user_id": "user-001", "resource_id": "resource-001", "urgency": "asap!!",
        })

        assert result.success is False
        assert "urgency" in result.errors[0].lower()

    def test_urgency_is_captured_even_in_ambiguous_results(self):
        agent = RequestUnderstandingAgent()
        result = agent.process({
            "user_id": "user-001", "resource_name": "e", "urgency": "high",
        })

        assert result.data["ambiguity_flag"] is True
        assert result.data["urgency"] == "high"


class TestRequestUnderstandingAgentFullIntegration:

    def test_realistic_multi_field_request_end_to_end(self):
        """
        Exercises Parts 1-3 together: a real user, resolving a resource
        by name (unique match), with explicit high urgency, feeding
        straight into a real FSM run.
        """
        from fsm.governance_fsm import GovernanceFSM
        from fsm.states import RequestState

        agent = RequestUnderstandingAgent()
        result = agent.process({
            "user_id": "user-010",  # Layla Hassan, Finance Director, clearance 4
            "resource_name": "Financial Report",  # resolves to resource-003, requires 3
            "urgency": "high",
        })

        assert result.success is True
        assert result.data["ambiguity_flag"] is False
        assert result.data["urgency"] == "high"
        assert result.data["resolved_resource_id"] == "resource-003"

        context = result.merge_into_context({"risk_score": 15})
        fsm = GovernanceFSM(request_id="integration-test-001")
        final_state = fsm.run_until_stuck(context)

        assert final_state == RequestState.CLOSED