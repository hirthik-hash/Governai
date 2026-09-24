# backend/agents/recovery_agent.py

"""
Failure Recovery Agent (GovernAI Agent 6 of 7).

Monitors system health and drives RecoveryFSM - built and frozen
Days 8-9 as part of v0.1-fsm-core, and until Day 55, exercised only
by manually-passed system_healthy/critical_failure flags in tests
for 45 straight days. This agent is RecoveryFSM's first genuine
caller.

Built across Days 54-58:

  - Three independent, injectable health checks (Day 54), mirroring
    SecurityRiskAgent's factor pattern:
      1. check_database_connectivity() - STUB, honestly labeled.
         No real database exists until Phase 3.
      2. check_agent_heartbeats() - REAL. Instantiates all 5 other
         agents; a genuine (if minimal) proxy for "is this agent's
         code in a working state."
      3. check_fsm_integrity() - REAL. Live version of Day 18's
         static rulebook-completeness tests, callable at runtime.

  - evaluate_and_transition() (Day 55): runs all checks and drives
    the injected RecoveryFSM. CRITICALITY POLICY: fsm_integrity
    failing is ALWAYS critical_failure=True (a broken rulebook is
    fundamental, not transient) - any other check failing alone is
    system_healthy=False but NOT automatically critical, giving the
    system room to self-recover via DEGRADED_WARNING before
    SAFE_MODE_ACTIVE triggers. Verified against multi-failure
    combinations and the full SAFE_MODE_ACTIVE -> RESTORING ->
    SYSTEM_NORMAL cycle, including restoration that fails and
    reverts (Day 57).

  - Live safe-mode gate integration (Day 56): proved the full real
    chain - genuine health-check failure -> RecoveryFSM -> shared
    instance -> SystemAwareRequestProcessor (Day 10) - blocks/allows
    requests correctly with ZERO manual FSM manipulation, the first
    test of that gate not to hand-force RecoveryFSM's state.

  - Six-agent chain integration (Day 58): the fullest chain built so
    far, covering both the healthy-system full lifecycle and the
    safe-mode-blocked case. Notable finding: a request blocked by
    the safe-mode gate has no natural mapping in
    AuditComplianceAgent's _DECISION_LABELS (GRANTED/DENIED) since
    the block happens outside the FSM's own state machine - it
    correctly falls through to PENDING, the honest answer, not a
    gap to paper over.

This agent does not itself decide access outcomes - it only reports
system health and drives RecoveryFSM's state. Access decisions
remain the FSM's alone, exactly as every other agent in this
project defers to it.
"""

from dataclasses import dataclass
from agents.base_agent import BaseAgent, AgentResult
from fsm.states import RequestState, SystemState, TERMINAL_REQUEST_STATES
from fsm.transitions import TRANSITIONS, SYSTEM_TRANSITIONS
from core.distributed_lock import DistributedLock, LockAcquisitionError
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

    def __init__(
        self,
        checks: list = None,
        recovery_fsm: RecoveryFSM = None,
        distributed_lock: DistributedLock = None,
    ):
        super().__init__()
        self._checks = checks or [
            check_database_connectivity,
            check_agent_heartbeats,
            check_fsm_integrity,
        ]
        self._recovery_fsm = recovery_fsm or RecoveryFSM()
        # Day 81: when the RecoveryFSM's state is shared (Redis-backed),
        # two workers could both call evaluate_and_transition() at nearly
        # the same moment and race on the SAME transition - e.g. both
        # reading SYSTEM_NORMAL and both trying to drive it to
        # DEGRADED_WARNING, one clobbering the other's write. This lock
        # (reusing core.distributed_lock.DistributedLock from Day 80)
        # serializes the transition step across workers. None (the
        # default, and every pre-Day-81 test) means no locking - fine for
        # a single process, where there is no one else to race against.
        self._distributed_lock = distributed_lock

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
            new_state, transitioned = self._locked_transition(context)
        except LockAcquisitionError:
            # Another worker is already evaluating/transitioning right
            # now - not an error, just nothing for THIS call to do. The
            # other worker's transition (if any) is still visible to us
            # on the next read of self._recovery_fsm.state, since state
            # is shared when a store is configured.
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

    def _locked_transition(self, context: dict):
        """Runs the actual FSM transition, under the distributed lock if one is configured."""
        if self._distributed_lock is None:
            return self._try_transition(context)
        with self._distributed_lock.acquire("recovery-fsm-transition"):
            return self._try_transition(context)

    def _try_transition(self, context: dict):
        try:
            return self._recovery_fsm.transition(context), True
        except Exception:
            # No valid transition from the current RecoveryFSM state for
            # this context (e.g. already healthy and nothing changed) -
            # not an error, just nothing to do.
            return self._recovery_fsm.state, False