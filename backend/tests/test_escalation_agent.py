# backend/tests/test_escalation_agent.py

from agents.escalation_agent import EscalationAgent


class TestEscalationAgentBasicRouting:

    def test_employee_routes_to_team_lead_when_sufficient(self):
        # user-001 (Alex Chen, clearance 1) reports to user-002 (Priya Nair, Team Lead, clearance 2)
        agent = EscalationAgent()
        result = agent.process({"user_id": "user-001", "required_clearance": 2})

        assert result.success is True
        assert result.data["approver_user_id"] == "user-002"

    def test_skips_insufficiently_cleared_approver_in_chain(self):
        # user-008 (Rina Sato, clearance 0) reports to user-002 (Priya Nair, clearance 2)
        # who reports to user-007 (CISO, clearance 5). Requesting
        # required_clearance=5 should skip Priya (only clearance 2) and land on CISO.
        agent = EscalationAgent()
        result = agent.process({"user_id": "user-008", "required_clearance": 5})

        assert result.data["approver_user_id"] == "user-007"

    def test_direct_manager_routed_to_when_exactly_sufficient(self):
        # user-003 (Marcus Webb, clearance 1) reports to user-004 (Sofia Ricci, clearance 3)
        agent = EscalationAgent()
        result = agent.process({"user_id": "user-003", "required_clearance": 3})

        assert result.data["approver_user_id"] == "user-004"


class TestEscalationAgentReachesTopOfChain:

    def test_reaching_ciso_flags_top_of_chain(self):
        agent = EscalationAgent()
        result = agent.process({"user_id": "user-008", "required_clearance": 5})

        assert result.data["escalation_reached_top_of_chain"] is True

    def test_non_top_approver_does_not_flag_top_of_chain(self):
        agent = EscalationAgent()
        result = agent.process({"user_id": "user-001", "required_clearance": 2})

        assert result.data["escalation_reached_top_of_chain"] is False

    def test_ciso_is_final_approver_even_for_impossible_clearance(self):
        """
        If required_clearance exceeds even the CISO's own clearance
        (5), the CISO is still returned as the final approver - there
        is nowhere higher to escalate to, so they're the ultimate
        decision-maker by default.
        """
        agent = EscalationAgent()
        result = agent.process({"user_id": "user-008", "required_clearance": 99})

        assert result.data["approver_user_id"] == "user-007"
        assert result.data["escalation_reached_top_of_chain"] is True


class TestEscalationAgentInvalidInput:

    def test_missing_user_id_fails_gracefully(self):
        agent = EscalationAgent()
        result = agent.process({"required_clearance": 3})

        assert result.success is False

    def test_missing_required_clearance_fails_gracefully(self):
        agent = EscalationAgent()
        result = agent.process({"user_id": "user-001"})

        assert result.success is False

    def test_unknown_user_id_fails_gracefully_not_raises(self):
        agent = EscalationAgent()
        result = agent.process({"user_id": "user-999", "required_clearance": 3})

        assert result.success is False


class TestEscalationAgentIntegratesWithPriorAgents:

    def test_chains_from_request_and_validation_agents(self):
        from agents.request_agent import RequestUnderstandingAgent

        request_agent = RequestUnderstandingAgent()
        request_result = request_agent.process({
            "user_id": "user-008",  # Rina Sato, clearance 0
            "resource_id": "resource-005",  # Salary Records, requires 5
        })

        escalation_agent = EscalationAgent()
        combined = dict(request_result.data)
        combined["user_id"] = "user-008"
        escalation_result = escalation_agent.process(combined)

        assert escalation_result.success is True
        assert escalation_result.data["approver_user_id"] == "user-007"