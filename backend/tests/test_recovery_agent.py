# backend/tests/test_recovery_agent.py

from agents.recovery_agent import (
    FailureRecoveryAgent,
    HealthCheckResult,
    check_database_connectivity,
    check_agent_heartbeats,
    check_fsm_integrity,
)


class TestDatabaseConnectivityStub:

    def test_always_reports_healthy(self):
        result = check_database_connectivity()
        assert result.healthy is True

    def test_detail_honestly_labels_itself_as_a_stub(self):
        result = check_database_connectivity()
        assert "STUB" in result.detail


class TestAgentHeartbeats:

    def test_all_five_agents_instantiate_successfully(self):
        result = check_agent_heartbeats()
        assert result.healthy is True
        assert "5" in result.detail

    def test_check_name_is_correct(self):
        result = check_agent_heartbeats()
        assert result.check_name == "agent_heartbeats"


class TestFsmIntegrity:

    def test_current_fsm_rulebook_is_healthy(self):
        """
        Confirms the live check agrees with Day 18's static test
        suite - the FSM rulebook should currently be fully sound.
        """
        result = check_fsm_integrity()
        assert result.healthy is True

    def test_detects_a_deliberately_broken_rulebook(self):
        """
        Proves the check actually detects problems, not just always
        returning healthy - constructs a fake broken rulebook inline
        rather than modifying the real one.
        """
        from fsm.states import RequestState
        from fsm.transitions import Transition

        # A state with NO outgoing transitions at all (broken)
        fake_transitions = [
            t for t in __import__("fsm.transitions", fromlist=["TRANSITIONS"]).TRANSITIONS
            if t.from_state != RequestState.PARSING_REQUEST
        ]

        import agents.recovery_agent as recovery_module
        original = recovery_module.TRANSITIONS
        recovery_module.TRANSITIONS = fake_transitions
        try:
            result = check_fsm_integrity()
            assert result.healthy is False
            assert "parsing_request" in result.detail.lower()
        finally:
            recovery_module.TRANSITIONS = original


class TestFailureRecoveryAgentAggregation:

    def test_all_checks_healthy_reports_overall_healthy(self):
        agent = FailureRecoveryAgent()
        result = agent.process({})

        assert result.success is True
        assert result.data["overall_healthy"] is True
        assert result.data["unhealthy_checks"] == []

    def test_check_results_includes_all_three_checks(self):
        agent = FailureRecoveryAgent()
        result = agent.process({})

        assert len(result.data["check_results"]) == 3
        assert all(isinstance(r, HealthCheckResult) for r in result.data["check_results"])

    def test_injected_failing_check_is_detected(self):
        def always_fails() -> HealthCheckResult:
            return HealthCheckResult(check_name="fake_check", healthy=False, detail="simulated failure")

        agent = FailureRecoveryAgent(checks=[always_fails])
        result = agent.process({})

        assert result.data["overall_healthy"] is False
        assert result.data["unhealthy_checks"] == ["fake_check"]

    def test_mixed_healthy_and_unhealthy_checks(self):
        def healthy_check() -> HealthCheckResult:
            return HealthCheckResult(check_name="ok", healthy=True, detail="fine")

        def unhealthy_check() -> HealthCheckResult:
            return HealthCheckResult(check_name="broken", healthy=False, detail="not fine")

        agent = FailureRecoveryAgent(checks=[healthy_check, unhealthy_check])
        result = agent.process({})

        assert result.data["overall_healthy"] is False
        assert result.data["unhealthy_checks"] == ["broken"]

    def test_custom_checks_list_is_used_instead_of_defaults(self):
        call_count = {"count": 0}

        def counting_check() -> HealthCheckResult:
            call_count["count"] += 1
            return HealthCheckResult(check_name="counter", healthy=True, detail="ok")

        agent = FailureRecoveryAgent(checks=[counting_check])
        agent.process({})

        assert call_count["count"] == 1
        assert len(agent.process({}).data["check_results"]) == 1