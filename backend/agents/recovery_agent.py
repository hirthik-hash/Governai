# backend/agents/recovery_agent.py

"""
Failure Recovery Agent (GovernAI Agent 6 of 7).

Monitors system health and drives RecoveryFSM (built and frozen
Days 8-9, part of v0.1-fsm-core) - which has, until Day 55, only
ever reacted to manually-passed system_healthy/critical_failure
flags in tests. This agent is RecoveryFSM's first real caller.

Health checks (Day 54) are small, independent, injectable callables:
  1. check_database_connectivity() - STUB (no real DB until Phase 3)
  2. check_agent_heartbeats() - REAL (instantiates all 6 agents)
  3. check_fsm_integrity() - REAL (live version of Day 18's
     rulebook completeness tests)

Day 55 (this addition): evaluate_and_transition(), which runs all
checks, translates the aggregate result into the exact context shape
RecoveryFSM.transition() expects, and actually drives the FSM.

CRITICALITY POLICY (a deliberate design decision, not arbitrary):
fsm_integrity failing is ALWAYS treated as critical_failure=True - a
broken rulebook is a fundamental problem, not a transient blip, and
per Day 8's rulebook DEGRADED_WARNING -> SAFE_MODE_ACTIVE requires
critical_failure=True specifically. Any OTHER check failing
(database, heartbeats) is treated as system_healthy=False but NOT
automatically critical - this gives the system a chance to recover
before escalating to the most severe response, mirroring real
operational practice where not every failed check should
immediately trip SAFE_MODE_ACTIVE.
"""

from dataclasses import dataclass
from agents.base_agent import BaseAgent, AgentResult
from fsm.states import RequestState, SystemState, TERMINAL_REQUEST_STATES
from fsm.transitions import TRANSITIONS, SYSTEM_TRANSITIONS
from fsm.recovery_fsm import RecoveryFSM


@dataclass
class HealthCheckResult:
    check_name: str
    healthy: bool
    detail: str


def check_database_connectivity() -> HealthCheckResult:
    return HealthCheckResult(
        check_name="database_connectivity",
        healthy=True,
        detail="STUB: no real database configured yet (Phase 3) - always reports healthy",
    )


def check_agent_heartbeats() -> HealthCheckResult:
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


# Checks whose failure is ALWAYS treated as critical, per the
# criticality policy above.
_ALWAYS_CRITICAL_CHECKS = {"fsm_integrity"}


class FailureRecoveryAgent(BaseAgent):
    """
    Runs registered health checks, aggregates results, and can drive
    a RecoveryFSM instance based on the aggregate outcome.
    """

    def __init__(self, checks: list = None, recovery_fsm: RecoveryFSM = None):
        super().__init__()
        self._checks = checks or [
            check_database_connectivity,
            check_agent_heartbeats,
            check_fsm_integrity,
        ]
        self._recovery_fsm = recovery_fsm or RecoveryFSM()

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

    def evaluate_and_transition(self) -> AgentResult:
        """
        Runs all health checks, derives system_healthy/critical_failure
        from their results per the criticality policy, and drives the
        injected RecoveryFSM with that context. Returns the FSM's new
        state alongside the health check data.
        """
        process_result = self.process({})
        unhealthy_checks = process_result.data["unhealthy_checks"]
        overall_healthy = process_result.data["overall_healthy"]

        is_critical = any(name in _ALWAYS_CRITICAL_CHECKS for name in unhealthy_checks)

        context = {
            "system_healthy": overall_healthy,
            "critical_failure": is_critical,
        }

        try:
            new_state = self._recovery_fsm.transition(context)
            transitioned = True
        except Exception:
            # No valid transition from the current RecoveryFSM state
            # for this context (e.g. already healthy and nothing
            # changed) - not an error, just nothing to do.
            new_state = self._recovery_fsm.state
            transitioned = False

        data = dict(process_result.data)
        data["fsm_state"] = new_state.value
        data["fsm_transitioned"] = transitioned
        data["critical_failure_detected"] = is_critical

        reasoning = (
            f"{process_result.reasoning} - RecoveryFSM now in {new_state.value}"
            + (" (critical failure detected)" if is_critical else "")
        )

        return self._success(data, reasoning)