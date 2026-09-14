# backend/tests/test_recovery_agent_safe_mode_integration.py

"""
Proves the full real chain: FailureRecoveryAgent detects a genuine
failure -> drives a shared RecoveryFSM into SAFE_MODE_ACTIVE ->
SystemAwareRequestProcessor (Day 10), sharing that SAME RecoveryFSM
instance, correctly blocks/allows requests based on it - with ZERO
manual FSM manipulation anywhere in these tests. Every prior test
touching the safe-mode gate (Days 10, 41, 47) hand-forced RecoveryFSM
into safe mode; this is the first test where a real health check
failure is what puts the system there.
"""

from datetime import datetime
from agents.recovery_agent import FailureRecoveryAgent, HealthCheckResult, check_agent_heartbeats, check_fsm_integrity
from fsm.recovery_fsm import RecoveryFSM
from fsm.governance_fsm import GovernanceFSM
from fsm.states import SystemState, RequestState
from core.orchestrator import SystemAwareRequestProcessor, AccessBlockedBySafeModeError
from agents.request_agent import RequestUnderstandingAgent
from agents.validation_agent import AccessValidationAgent
from agents.security_agent import SecurityRiskAgent


class TestRealFailureBlocksRealRequest:

    def test_genuine_critical_failure_blocks_a_normal_request(self):
        """
        The full real chain: a genuinely failing fsm_integrity check
        (always critical per Day 55's policy) drives the shared
        RecoveryFSM to SAFE_MODE_ACTIVE, and a normal (non-public)
        request that would otherwise be authorized gets blocked by
        SystemAwareRequestProcessor consulting that same instance.
        """
        shared_fsm = RecoveryFSM(initial_state=SystemState.DEGRADED_WARNING)

        def failing_integrity() -> HealthCheckResult:
            return HealthCheckResult(check_name="fsm_integrity", healthy=False, detail="simulated break")

        recovery_agent = FailureRecoveryAgent(checks=[failing_integrity], recovery_fsm=shared_fsm)
        recovery_result = recovery_agent.evaluate_and_transition()
        assert recovery_result.data["fsm_state"] == "safe_mode_active"

        processor = SystemAwareRequestProcessor(shared_fsm)

        request_fsm = GovernanceFSM(request_id="req-safe-mode-real-001")
        context = {
            "ambiguity_flag": False, "clearance": 5, "required_clearance": 1, "risk_score": 5,
        }

        try:
            processor.process_until_stuck(request_fsm, context)
            assert False, "Expected AccessBlockedBySafeModeError"
        except AccessBlockedBySafeModeError:
            pass

    def test_genuine_critical_failure_still_allows_public_readonly_request(self):
        """
        Same real failure chain, but the request is flagged
        public/read-only - should sail through per Day 10's design,
        exactly like the manually-forced version did, but this time
        driven by a real health check failure.
        """
        shared_fsm = RecoveryFSM(initial_state=SystemState.DEGRADED_WARNING)

        def failing_integrity() -> HealthCheckResult:
            return HealthCheckResult(check_name="fsm_integrity", healthy=False, detail="simulated break")

        recovery_agent = FailureRecoveryAgent(checks=[failing_integrity], recovery_fsm=shared_fsm)
        recovery_agent.evaluate_and_transition()

        processor = SystemAwareRequestProcessor(shared_fsm)
        request_fsm = GovernanceFSM(request_id="req-safe-mode-real-002")
        context = {
            "ambiguity_flag": False, "clearance": 5, "required_clearance": 1,
            "risk_score": 5, "is_public_readonly": True,
        }

        final_state = processor.process_until_stuck(request_fsm, context)

        assert final_state == RequestState.CLOSED


class TestRealNonCriticalFailureDoesNotBlock:

    def test_non_critical_failure_reaches_degraded_warning_not_safe_mode_so_requests_still_flow(self):
        """
        A database-only failure (not fsm_integrity) should only reach
        DEGRADED_WARNING, not SAFE_MODE_ACTIVE - so
        SystemAwareRequestProcessor should NOT block anything, since
        its gate only checks is_safe_mode(), which is False in
        DEGRADED_WARNING.
        """
        shared_fsm = RecoveryFSM()  # starts in SYSTEM_NORMAL

        def failing_database() -> HealthCheckResult:
            return HealthCheckResult(check_name="database_connectivity", healthy=False, detail="down")

        recovery_agent = FailureRecoveryAgent(
            checks=[failing_database, check_agent_heartbeats, check_fsm_integrity],
            recovery_fsm=shared_fsm,
        )
        recovery_result = recovery_agent.evaluate_and_transition()
        assert recovery_result.data["fsm_state"] == "degraded_warning"

        processor = SystemAwareRequestProcessor(shared_fsm)
        request_fsm = GovernanceFSM(request_id="req-degraded-not-blocked-001")
        context = {
            "ambiguity_flag": False, "clearance": 5, "required_clearance": 1, "risk_score": 5,
        }

        final_state = processor.process_until_stuck(request_fsm, context)

        assert final_state == RequestState.CLOSED


class TestFullFiveAgentChainDuringRealSafeMode:

    def test_full_agent_chain_request_blocked_during_real_safe_mode(self):
        """
        The most complete version yet: a genuine health-check failure
        drives real safe mode, then a request runs through ALL of
        RequestUnderstandingAgent, AccessValidationAgent, and
        SecurityRiskAgent before hitting the safe-mode gate - proving
        the block happens regardless of how much real agent
        processing preceded it.
        """
        shared_fsm = RecoveryFSM(initial_state=SystemState.DEGRADED_WARNING)

        def failing_integrity() -> HealthCheckResult:
            return HealthCheckResult(check_name="fsm_integrity", healthy=False, detail="simulated break")

        recovery_agent = FailureRecoveryAgent(checks=[failing_integrity], recovery_fsm=shared_fsm)
        recovery_agent.evaluate_and_transition()
        assert shared_fsm.is_safe_mode() is True

        request_agent = RequestUnderstandingAgent()
        request_result = request_agent.process({
            "user_id": "user-007", "resource_id": "resource-001",  # would normally authorize easily
        })

        fixed_now = lambda: datetime(2026, 9, 9, 14, 0)
        validation_agent = AccessValidationAgent(now_fn=fixed_now)
        combined = dict(request_result.data)
        combined["session_token"] = "abc"
        validation_result = validation_agent.process(combined)
        combined.update(validation_result.data)

        security_agent = SecurityRiskAgent()
        combined["user_id"] = "user-007"
        security_result = security_agent.process(combined)
        combined.update(security_result.data)

        processor = SystemAwareRequestProcessor(shared_fsm)
        request_fsm = GovernanceFSM(request_id="req-full-chain-safe-mode-001")

        try:
            processor.process_until_stuck(request_fsm, combined)
            assert False, "Expected AccessBlockedBySafeModeError despite a normally-authorizing request"
        except AccessBlockedBySafeModeError:
            pass