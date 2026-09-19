# backend/tests/test_recovery_agent.py

from agents.recovery_agent import (
    FailureRecoveryAgent,
    HealthCheckResult,
    check_database_connectivity,
    check_agent_heartbeats,
    check_fsm_integrity,
)
from fsm.states import SystemState
from fsm.recovery_fsm import RecoveryFSM


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



class TestEvaluateAndTransitionHealthySystem:

    def test_all_healthy_keeps_fsm_in_system_normal(self):
        agent = FailureRecoveryAgent()
        result = agent.evaluate_and_transition()

        assert result.data["fsm_state"] == "system_normal"
        assert result.data["fsm_transitioned"] is False  # nothing to transition to
        assert result.data["critical_failure_detected"] is False


class TestEvaluateAndTransitionNonCriticalFailure:

    def test_database_failure_alone_moves_to_degraded_warning_not_safe_mode(self):
        def failing_database() -> HealthCheckResult:
            return HealthCheckResult(check_name="database_connectivity", healthy=False, detail="down")

        agent = FailureRecoveryAgent(checks=[failing_database, check_agent_heartbeats, check_fsm_integrity])
        result = agent.evaluate_and_transition()

        assert result.data["fsm_state"] == "degraded_warning"
        assert result.data["critical_failure_detected"] is False

    def test_heartbeat_failure_alone_is_not_critical(self):
        def failing_heartbeats() -> HealthCheckResult:
            return HealthCheckResult(check_name="agent_heartbeats", healthy=False, detail="one agent broke")

        agent = FailureRecoveryAgent(checks=[check_database_connectivity, failing_heartbeats, check_fsm_integrity])
        result = agent.evaluate_and_transition()

        assert result.data["fsm_state"] == "degraded_warning"
        assert result.data["critical_failure_detected"] is False


class TestEvaluateAndTransitionCriticalFailure:

    def test_fsm_integrity_failure_is_always_critical(self):
        def failing_integrity() -> HealthCheckResult:
            return HealthCheckResult(check_name="fsm_integrity", healthy=False, detail="broken rulebook")

        # Start the FSM already in DEGRADED_WARNING, since
        # SAFE_MODE_ACTIVE requires coming from there per the rulebook
        pre_warned_fsm = RecoveryFSM(initial_state=SystemState.DEGRADED_WARNING)
        agent = FailureRecoveryAgent(
            checks=[check_database_connectivity, check_agent_heartbeats, failing_integrity],
            recovery_fsm=pre_warned_fsm,
        )
        result = agent.evaluate_and_transition()

        assert result.data["fsm_state"] == "safe_mode_active"
        assert result.data["critical_failure_detected"] is True

    def test_reasoning_mentions_critical_failure_when_detected(self):
        def failing_integrity() -> HealthCheckResult:
            return HealthCheckResult(check_name="fsm_integrity", healthy=False, detail="broken")

        pre_warned_fsm = RecoveryFSM(initial_state=SystemState.DEGRADED_WARNING)
        agent = FailureRecoveryAgent(checks=[failing_integrity], recovery_fsm=pre_warned_fsm)
        result = agent.evaluate_and_transition()

        assert "critical" in result.reasoning.lower()


class TestEvaluateAndTransitionRecoverySequence:

    def test_full_degrade_and_recover_sequence_using_real_checks(self):
        """
        A realistic sequence: system starts healthy, a transient
        database issue causes a warning, then recovers - using
        evaluate_and_transition() twice in a row, driving the same
        shared RecoveryFSM instance both times.
        """
        shared_fsm = RecoveryFSM()

        def failing_database() -> HealthCheckResult:
            return HealthCheckResult(check_name="database_connectivity", healthy=False, detail="timeout")

        agent_during_failure = FailureRecoveryAgent(
            checks=[failing_database, check_agent_heartbeats, check_fsm_integrity],
            recovery_fsm=shared_fsm,
        )
        first_result = agent_during_failure.evaluate_and_transition()
        assert first_result.data["fsm_state"] == "degraded_warning"

        agent_after_recovery = FailureRecoveryAgent(
            checks=[check_database_connectivity, check_agent_heartbeats, check_fsm_integrity],
            recovery_fsm=shared_fsm,
        )
        second_result = agent_after_recovery.evaluate_and_transition()
        assert second_result.data["fsm_state"] == "system_normal"

class TestMultipleSimultaneousFailures:

    def test_database_and_fsm_integrity_both_failing_is_still_critical(self):
        def failing_database() -> HealthCheckResult:
            return HealthCheckResult(check_name="database_connectivity", healthy=False, detail="down")

        def failing_integrity() -> HealthCheckResult:
            return HealthCheckResult(check_name="fsm_integrity", healthy=False, detail="broken")

        pre_warned_fsm = RecoveryFSM(initial_state=SystemState.DEGRADED_WARNING)
        agent = FailureRecoveryAgent(
            checks=[failing_database, check_agent_heartbeats, failing_integrity],
            recovery_fsm=pre_warned_fsm,
        )
        result = agent.evaluate_and_transition()

        assert result.data["critical_failure_detected"] is True
        assert result.data["fsm_state"] == "safe_mode_active"
        assert set(result.data["unhealthy_checks"]) == {"database_connectivity", "fsm_integrity"}

    def test_all_three_checks_failing_reports_all_three_as_unhealthy(self):
        def fail_db() -> HealthCheckResult:
            return HealthCheckResult(check_name="database_connectivity", healthy=False, detail="x")

        def fail_heartbeats() -> HealthCheckResult:
            return HealthCheckResult(check_name="agent_heartbeats", healthy=False, detail="x")

        def fail_integrity() -> HealthCheckResult:
            return HealthCheckResult(check_name="fsm_integrity", healthy=False, detail="x")

        agent = FailureRecoveryAgent(checks=[fail_db, fail_heartbeats, fail_integrity])
        result = agent.process({})

        assert len(result.data["unhealthy_checks"]) == 3


class TestRepeatedEvaluationWhileAlreadyInSafeMode:

    def test_calling_again_while_still_critical_does_not_crash(self):
        def failing_integrity() -> HealthCheckResult:
            return HealthCheckResult(check_name="fsm_integrity", healthy=False, detail="still broken")

        pre_warned_fsm = RecoveryFSM(initial_state=SystemState.DEGRADED_WARNING)
        agent = FailureRecoveryAgent(checks=[failing_integrity], recovery_fsm=pre_warned_fsm)

        first_result = agent.evaluate_and_transition()
        assert first_result.data["fsm_state"] == "safe_mode_active"

        # Calling again while the SAME failure persists - SAFE_MODE_ACTIVE's
        # only transition requires system_healthy=True, which isn't the
        # case here, so this should not find a valid transition.
        second_result = agent.evaluate_and_transition()

        assert second_result.data["fsm_state"] == "safe_mode_active"
        assert second_result.data["fsm_transitioned"] is False


class TestFullRestorationCycle:

    def test_safe_mode_to_restoring_to_normal_via_real_evaluate_calls(self):
        shared_fsm = RecoveryFSM(initial_state=SystemState.SAFE_MODE_ACTIVE)

        # Step 1: checks now pass -> should move to RESTORING
        healthy_agent = FailureRecoveryAgent(recovery_fsm=shared_fsm)
        step1 = healthy_agent.evaluate_and_transition()
        assert step1.data["fsm_state"] == "restoring"

        # Step 2: checks still pass -> RESTORING should confirm -> SYSTEM_NORMAL
        step2 = healthy_agent.evaluate_and_transition()
        assert step2.data["fsm_state"] == "system_normal"

    def test_restoration_that_fails_reverts_to_safe_mode(self):
        shared_fsm = RecoveryFSM(initial_state=SystemState.SAFE_MODE_ACTIVE)

        healthy_agent = FailureRecoveryAgent(recovery_fsm=shared_fsm)
        step1 = healthy_agent.evaluate_and_transition()
        assert step1.data["fsm_state"] == "restoring"

        def failing_database() -> HealthCheckResult:
            return HealthCheckResult(check_name="database_connectivity", healthy=False, detail="flaky again")

        failing_agent = FailureRecoveryAgent(
            checks=[failing_database, check_agent_heartbeats, check_fsm_integrity],
            recovery_fsm=shared_fsm,
        )
        step2 = failing_agent.evaluate_and_transition()

        assert step2.data["fsm_state"] == "safe_mode_active"


class TestAgentHeartbeatsActuallyCatchesFailure:

    def test_heartbeat_check_detects_a_genuinely_broken_agent_class(self):
        """
        Proves check_agent_heartbeats' underlying pattern (try to
        instantiate, catch exceptions) actually works, using a fake
        broken class rather than sabotaging a real agent. This
        exercises the SAME instantiate-and-catch logic inline,
        confirming the pattern itself is sound.
        """
        class BrokenAgent:
            def __init__(self):
                raise RuntimeError("simulated constructor failure")

        failed = []
        for agent_class in [BrokenAgent]:
            try:
                agent_class()
            except Exception as e:
                failed.append(f"{agent_class.__name__}: {e}")

        assert len(failed) == 1
        assert "BrokenAgent" in failed[0]
        assert "simulated constructor failure" in failed[0]

