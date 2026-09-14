# backend/agents/recovery_agent.py

"""
Failure Recovery Agent (GovernAI Agent 6 of 7).

Monitors system health and drives RecoveryFSM (built and frozen
Days 8-9, part of v0.1-fsm-core) - which has, until today, only ever
reacted to manually-passed system_healthy/critical_failure flags in
tests. This agent is RecoveryFSM's first real caller.

Health checks are small, independent, injectable callables - not one
monolithic method - mirroring SecurityRiskAgent's factor pattern
(Days 36-38). Today's three checks (Day 54):

  1. check_database_connectivity() - STUB. No real database exists
     yet (Phase 3). Honestly labeled as a stub that always reports
     healthy, not disguised as a real check - faking database
     connectivity now would be dishonest scope creep.

  2. check_agent_heartbeats() - REAL. Attempts to instantiate each
     of the 5 agents built so far (Days 25-53). A cheap, genuine
     proxy for "is this agent's code in a working state" - if an
     agent's constructor raises, something is actually broken.

  3. check_fsm_integrity() - REAL. Reuses the same completeness
     logic from tests/test_rulebook_completeness.py (Day 18) as a
     live, callable check - every non-terminal state has an outgoing
     transition, no orphaned states - rather than only a one-time
     test assertion.

Days 55+ will wire these into actual RecoveryFSM transitions and add
the remaining roadmap checks (API response times has no real API
yet either - Phase 3 - so it's deferred, not faked).
"""

from dataclasses import dataclass
from agents.base_agent import BaseAgent, AgentResult
from fsm.states import RequestState, SystemState, TERMINAL_REQUEST_STATES
from fsm.transitions import TRANSITIONS, SYSTEM_TRANSITIONS


@dataclass
class HealthCheckResult:
    check_name: str
    healthy: bool
    detail: str


def check_database_connectivity() -> HealthCheckResult:
    """
    STUB - no real database exists yet (Phase 3, Days 71-73 add real
    persistence). Always reports healthy. Honestly labeled as a stub
    in both the docstring and the detail message, not disguised as
    a real check.
    """
    return HealthCheckResult(
        check_name="database_connectivity",
        healthy=True,
        detail="STUB: no real database configured yet (Phase 3) - always reports healthy",
    )


def check_agent_heartbeats() -> HealthCheckResult:
    """
    Attempts to instantiate each of the 5 agents built so far. A
    genuine, if minimal, proxy for 'is this agent's code in a
    working state' - a broken import or constructor error would
    surface here as a real failure, not a simulated one.
    """
    from agents.request_agent import RequestUnderstandingAgent
    from agents.validation_agent import AccessValidationAgent
    from agents.security_agent import SecurityRiskAgent
    from agents.escalation_agent import EscalationAgent
    from agents.audit_agent import AuditComplianceAgent

    agent_classes = [
        RequestUnderstandingAgent, AccessValidationAgent, SecurityRiskAgent,
        EscalationAgent, AuditComplianceAgent,
    ]

    failed = []
    for agent_class in agent_classes:
        try:
            agent_class()
        except Exception as e:
            failed.append(f"{agent_class.__name__}: {e}")

    if failed:
        return HealthCheckResult(
            check_name="agent_heartbeats",
            healthy=False,
            detail=f"{len(failed)} agent(s) failed to instantiate: {'; '.join(failed)}",
        )

    return HealthCheckResult(
        check_name="agent_heartbeats",
        healthy=True,
        detail=f"All {len(agent_classes)} agents instantiated successfully",
    )


def check_fsm_integrity() -> HealthCheckResult:
    """
    Live version of Day 18's rulebook completeness tests: every
    non-terminal RequestState/SystemState must have at least one
    outgoing transition, and terminal states must have none. Reuses
    the exact same logic as tests/test_rulebook_completeness.py, but
    callable at runtime rather than only as a one-time test.
    """
    problems = []

    for state in RequestState:
        outgoing = [t for t in TRANSITIONS if t.from_state == state]
        if state in TERMINAL_REQUEST_STATES:
            if outgoing:
                problems.append(f"{state.value} is terminal but has outgoing transitions")
        else:
            if not outgoing:
                problems.append(f"{state.value} is non-terminal but has no outgoing transitions")

    for state in SystemState:
        outgoing = [t for t in SYSTEM_TRANSITIONS if t.from_state == state]
        if not outgoing:
            problems.append(f"{state.value} (system) has no outgoing transitions")

    if problems:
        return HealthCheckResult(
            check_name="fsm_integrity",
            healthy=False,
            detail=f"{len(problems)} rulebook problem(s): {'; '.join(problems)}",
        )

    return HealthCheckResult(
        check_name="fsm_integrity",
        healthy=True,
        detail="All FSM states have correct outgoing transitions",
    )


class FailureRecoveryAgent(BaseAgent):
    """
    Runs all registered health checks and aggregates their results.
    Does not yet drive RecoveryFSM directly - that wiring is Day 55.
    Today's process() only reports aggregate health.
    """

    def __init__(self, checks: list = None):
        super().__init__()
        self._checks = checks or [
            check_database_connectivity,
            check_agent_heartbeats,
            check_fsm_integrity,
        ]

    @property
    def agent_name(self) -> str:
        return "failure_recovery"

    def process(self, input_data: dict) -> AgentResult:
        results = [check() for check in self._checks]
        unhealthy = [r for r in results if not r.healthy]

        overall_healthy = len(unhealthy) == 0

        data = {
            "overall_healthy": overall_healthy,
            "check_results": results,
            "unhealthy_checks": [r.check_name for r in unhealthy],
        }

        if overall_healthy:
            reasoning = f"All {len(results)} health checks passed"
        else:
            reasoning = (
                f"{len(unhealthy)} of {len(results)} health checks failed: "
                f"{', '.join(r.check_name for r in unhealthy)}"
            )

        return self._success(data, reasoning)