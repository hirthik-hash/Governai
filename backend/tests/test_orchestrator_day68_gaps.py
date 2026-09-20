# backend/tests/test_orchestrator_day68_gaps.py

"""
Day 68: closes the three MEANINGFUL coverage gaps identified in Day
66's review of core/orchestrator.py (96% coverage, lines 182, 202,
224-226, 280-287 uncovered):

  - Line 182: SecurityRiskAgent.process() failing inside
    submit_request() - never exercised, since every prior test's
    context reaches SecurityRiskAgent with valid data.
  - Line 202: EscalationAgent.process() failing inside
    submit_request() - same gap, one stage later in the chain.
  - Lines 280-287: resolve_escalation() hitting the safe-mode gate -
    every prior resolve_escalation() test assumed a healthy system.

Lines 224-226 (the "stuck in unexpected state" defensive branch) are
DELIBERATELY left uncovered - forcing a fake broken FSM state just to
hit a safety-net line that should never trigger under correct code
would test nothing real.
"""

import pytest
from unittest.mock import patch
from core.orchestrator import RequestPipeline
from agents.base_agent import AgentResult
from fsm.recovery_fsm import RecoveryFSM
from fsm.states import SystemState


class TestSecurityRiskAgentFailureInSubmitRequest:

    def test_security_agent_failure_returns_error_status(self):
        """
        Mocks SecurityRiskAgent.process() to fail directly - the
        cleanest way to trigger line 182's branch without needing to
        contrive real input data that would naturally break it,
        since SecurityRiskAgent is deliberately built to almost never
        fail on well-formed input (Day 36-40's design).
        """
        pipeline = RequestPipeline()

        with patch.object(
            pipeline.security_agent, "process",
            return_value=AgentResult(success=False, data={}, reasoning="forced failure", errors=["simulated security agent failure"]),
        ):
            result = pipeline.submit_request({
                "request_id": "req-day68-security-fail",
                "user_id": "user-007", "resource_id": "resource-001",
                "session_token": "abc",
            })

        assert result.status == "error"
        assert "simulated security agent failure" in result.errors


class TestEscalationAgentFailureInSubmitRequest:

    def test_escalation_agent_failure_returns_error_status(self):
        """
        Same technique for line 202: a request that genuinely reaches
        ESCALATION_REQUIRED (user-003/resource-003, real data), with
        EscalationAgent.process() itself mocked to fail - isolating
        the failure to exactly this one branch.
        """
        pipeline = RequestPipeline()

        with patch.object(
            pipeline.escalation_agent, "process",
            return_value=AgentResult(success=False, data={}, reasoning="forced failure", errors=["simulated escalation agent failure"]),
        ):
            result = pipeline.submit_request({
                "request_id": "req-day68-escalation-fail",
                "user_id": "user-003", "resource_id": "resource-003",
                "session_token": "abc",
            })

        assert result.status == "error"
        assert "simulated escalation agent failure" in result.errors


class TestResolveEscalationDuringSafeMode:

    def test_resolving_an_approved_escalation_during_safe_mode_is_blocked(self):
        """
        Lines 280-287: every resolve_escalation() test since Day 61
        assumed a healthy system. Here the system is genuinely in
        SAFE_MODE_ACTIVE by the time resolution happens, confirming
        the same safe-mode gate that protects submit_request() also
        protects resolve_escalation() - and that an audit record is
        still produced for the blocked attempt, not silently dropped.
        """
        shared_fsm = RecoveryFSM(initial_state=SystemState.SAFE_MODE_ACTIVE)
        pipeline = RequestPipeline(recovery_fsm=shared_fsm)

        # Submit while healthy is not possible here since the FSM is
        # already forced into safe mode - so we submit an escalating
        # request first with a HEALTHY fsm, get it pending, THEN flip
        # to safe mode before resolving. This matches the realistic
        # sequence: escalate now, safe mode kicks in later, approver
        # resolves after the fact.
        healthy_fsm = RecoveryFSM()
        pipeline_healthy = RequestPipeline(recovery_fsm=healthy_fsm)
        pipeline_healthy.submit_request({
            "request_id": "req-day68-safemode-resolve",
            "user_id": "user-003", "resource_id": "resource-003",
            "session_token": "abc",
        })

        # Now force that SAME pipeline's fsm into safe mode before resolving.
        healthy_fsm.transition({"system_healthy": False})
        healthy_fsm.transition({"critical_failure": True})

        result = pipeline_healthy.resolve_escalation("req-day68-safemode-resolve", human_decision="approved")

        assert result.status == "blocked_safe_mode"
        assert result.audit_record is not None