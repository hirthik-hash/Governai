# backend/core/orchestrator.py

"""
Orchestration layer for GovernAI.

SystemAwareRequestProcessor (Day 10): the safe-mode gate sitting
between a single request's GovernanceFSM and the shared RecoveryFSM -
unchanged since Day 10, still the sole authority on blocking access
during SAFE_MODE_ACTIVE.

RequestPipeline (Days 60-61): formalizes the six-agent chaining
pattern every integration test since Day 41 has hand-built -
RequestUnderstandingAgent -> AccessValidationAgent ->
SecurityRiskAgent -> (EscalationAgent, if needed) ->
AuditComplianceAgent - into one reusable class, with
FailureRecoveryAgent-driven RecoveryFSM enforcing the safe-mode gate
throughout.

Day 60 (this file): submit_request() covers the direct-outcome path
(granted/denied/blocked/clarification-needed) and escalation
INITIATION - calling EscalationAgent.process() and parking the
request in an in-memory pending-requests store, since resolution
depends on a human decision that hasn't happened yet.

Day 61 adds resolve_escalation() to complete the loop.

Pending-request storage is an in-memory dict, keyed by request_id -
an honest Phase-3 gap (Days 71-73 add real persistence), sufficient
today to prove the pattern, not to be production-durable.
"""

from dataclasses import dataclass, field
from fsm.governance_fsm import GovernanceFSM
from fsm.states import RequestState, TERMINAL_REQUEST_STATES
from fsm.recovery_fsm import RecoveryFSM
from fsm.transitions import manager_approved
from core.decision_logger import DecisionLogger
from core.request_history_tracker import RequestHistoryTracker
from core.geo_anomaly_detector import GeoAnomalyDetector
from core.timeout_tracker import EscalationTimeoutTracker
from agents.request_agent import RequestUnderstandingAgent
from agents.validation_agent import AccessValidationAgent
from agents.security_agent import SecurityRiskAgent
from agents.escalation_agent import EscalationAgent
from agents.audit_agent import AuditComplianceAgent
from agents.base_agent import AgentResult


class AccessBlockedBySafeModeError(Exception):
    pass


class SystemAwareRequestProcessor:
    """
    Coordinates a single request's GovernanceFSM against the shared
    RecoveryFSM's health state (Day 10). Refuses to let a request
    reach ACCESS_GRANTED while the system is in SAFE_MODE_ACTIVE,
    unless the request is flagged public/read-only.
    """

    def __init__(self, recovery_fsm: RecoveryFSM, logger: DecisionLogger = None):
        self.recovery_fsm = recovery_fsm
        self.logger = logger or DecisionLogger()

    def _is_grant_attempt(self, request_fsm: GovernanceFSM, context: dict) -> bool:
        """
        True if the next transition would move this request toward
        ACCESS_GRANTED. There are exactly two such paths:
          - AUTHORIZED -> ACCESS_GRANTED (direct authorization)
          - MANAGER_REVIEW -> ACCESS_GRANTED (approved escalation)
        Day 68 fix: this originally checked only the first path, so an
        approved escalation was never gated by safe mode. Rejections
        and timeouts are deliberately NOT grant attempts - denying
        access during safe mode is always safe.
        """
        if request_fsm.state == RequestState.AUTHORIZED:
            return True
        if request_fsm.state == RequestState.MANAGER_REVIEW and manager_approved(context):
            return True
        return False

    def process(self, request_fsm: GovernanceFSM, context: dict) -> RequestState:
        if self._is_grant_attempt(request_fsm, context) and self.recovery_fsm.is_safe_mode():
            if not context.get("is_public_readonly", False):
                message = (
                    f"Request {request_fsm.request_id} would be granted access, "
                    f"but system is in SAFE_MODE_ACTIVE and request is not "
                    f"public/read-only. Access blocked, request queued."
                )
                self.logger.log_safe_mode_block(request_fsm.request_id, message, context)
                raise AccessBlockedBySafeModeError(message)

        new_state = request_fsm.transition(context)
        self.logger.log_request_transition(request_fsm.request_id, request_fsm.history[-1])
        return new_state

    def process_until_stuck(self, request_fsm: GovernanceFSM, context: dict) -> RequestState:
        while (
            request_fsm.state not in TERMINAL_REQUEST_STATES
            and request_fsm.can_transition(context)
        ):
            self.process(request_fsm, context)
        return request_fsm.state

    def transition_system(self, context: dict):
        new_state = self.recovery_fsm.transition(context)
        self.logger.log_system_transition(self.recovery_fsm.history[-1])
        return new_state


@dataclass
class PipelineResult:
    """
    The orchestrator's single, unambiguous answer per request -
    status is one of: "granted", "denied", "clarification_needed",
    "pending_approval", "blocked_safe_mode", "error".
    """
    request_id: str
    status: str
    fsm_state: str = ""
    candidate_resource_ids: list = field(default_factory=list)
    notification: object = None
    audit_record: object = None
    errors: list = field(default_factory=list)


class RequestPipeline:
    """
    Wires all six built agents together into one reusable pipeline.
    Agent 7 (Policy Intelligence) is not part of this yet - it's
    deferred to Phase 4.
    """

    def __init__(
        self,
        recovery_fsm: RecoveryFSM = None,
        history_tracker: RequestHistoryTracker = None,
        geo_detector: GeoAnomalyDetector = None,
        timeout_tracker: EscalationTimeoutTracker = None,
        decision_logger: DecisionLogger = None,
    ):
        self.request_agent = RequestUnderstandingAgent()
        self.validation_agent = AccessValidationAgent()
        self.security_agent = SecurityRiskAgent(
            history_tracker=history_tracker or RequestHistoryTracker(),
            geo_detector=geo_detector or GeoAnomalyDetector(),
        )
        self.escalation_agent = EscalationAgent(
            timeout_tracker=timeout_tracker or EscalationTimeoutTracker(),
        )
        self.audit_agent = AuditComplianceAgent()

        self.recovery_fsm = recovery_fsm or RecoveryFSM()
        self.decision_logger = decision_logger or DecisionLogger()
        self.safe_mode_processor = SystemAwareRequestProcessor(self.recovery_fsm, self.decision_logger)

        # In-memory pending-requests store: request_id -> (GovernanceFSM, context dict, [AgentResult, ...])
        self._pending_requests: dict = {}

    def submit_request(self, request_data: dict) -> PipelineResult:
        request_id = request_data.get("request_id", f"req-{id(request_data)}")

        agent_results: list = []

        request_result = self.request_agent.process(request_data)
        agent_results.append(request_result)
        if not request_result.success:
            return PipelineResult(request_id=request_id, status="error", errors=request_result.errors)

        if request_result.data.get("ambiguity_flag") is True:
            return PipelineResult(
                request_id=request_id,
                status="clarification_needed",
                candidate_resource_ids=request_result.data.get("candidate_resource_ids", []),
            )

        combined = dict(request_result.data)
        combined["user_id"] = request_data["user_id"]
        combined["request_id"] = request_id

        validation_input = dict(combined)
        validation_input["session_token"] = request_data.get("session_token")
        if "session_expired" in request_data:
            validation_input["session_expired"] = request_data["session_expired"]
        if "role" in request_data:
            validation_input["role"] = request_data["role"]
        if "is_public_readonly" in request_data:
            combined["is_public_readonly"] = request_data["is_public_readonly"]
        validation_result = self.validation_agent.process(validation_input)
        agent_results.append(validation_result)
        if not validation_result.success:
            return PipelineResult(request_id=request_id, status="error", errors=validation_result.errors)
        combined.update(validation_result.data)

        if request_data.get("location"):
            combined["location"] = request_data["location"]
        security_result = self.security_agent.process(combined)
        agent_results.append(security_result)
        if not security_result.success:
            return PipelineResult(request_id=request_id, status="error", errors=security_result.errors)
        combined.update(security_result.data)

        request_fsm = GovernanceFSM(request_id=request_id)

        try:
            state = self.safe_mode_processor.process_until_stuck(request_fsm, combined)
        except AccessBlockedBySafeModeError as e:
            audit_record = self._compile_audit(request_id, combined, "blocked_safe_mode", agent_results)
            return PipelineResult(
                request_id=request_id, status="blocked_safe_mode",
                fsm_state="blocked_safe_mode", audit_record=audit_record, errors=[str(e)],
            )

        if state == RequestState.ESCALATION_REQUIRED:
            escalation_input = dict(combined)
            escalation_result = self.escalation_agent.process(escalation_input)
            agent_results.append(escalation_result)

            if not escalation_result.success:
                return PipelineResult(request_id=request_id, status="error", errors=escalation_result.errors)

            combined.update(escalation_result.data)
            state = self.safe_mode_processor.process(request_fsm, combined)  # -> MANAGER_REVIEW

            self._pending_requests[request_id] = (request_fsm, combined, agent_results)

            return PipelineResult(
                request_id=request_id, status="pending_approval",
                fsm_state=state.value, notification=escalation_result.data.get("notification"),
            )

        if state in TERMINAL_REQUEST_STATES:
            audit_record = self._compile_audit(request_id, combined, state.value, agent_results)
            status = "granted" if state == RequestState.CLOSED else "denied"
            return PipelineResult(
                request_id=request_id, status=status, fsm_state=state.value, audit_record=audit_record,
            )

        # Stuck in a non-terminal, non-escalation state - a genuine
        # pipeline bug, not an expected outcome, so this is reported
        # as an error rather than silently returned as any known status.
        return PipelineResult(
            request_id=request_id, status="error",
            fsm_state=state.value, errors=[f"Pipeline stuck in unexpected state: {state.value}"],
        )

    def _compile_audit(self, request_id: str, combined: dict, final_fsm_state: str, agent_results: list):
        audit_input = dict(combined)
        audit_input["request_id"] = request_id
        audit_input["final_fsm_state"] = final_fsm_state
        audit_input["agent_results"] = agent_results
        audit_result = self.audit_agent.process(audit_input)
        return audit_result.data.get("audit_record") if audit_result.success else None

    def resolve_escalation(self, request_id: str, human_decision: str = None) -> PipelineResult:
        """
        Completes a request previously parked by submit_request()
        when it reached ESCALATION_REQUIRED. human_decision is
        "approved", "rejected", or None (checks the timeout tracker
        instead) - see EscalationAgent.resolve_decision() (Day 45)
        for the exact semantics, including the Day 46 policy that a
        late human decision always overrides an expired timeout.
        """
        if request_id not in self._pending_requests:
            return PipelineResult(
                request_id=request_id,
                status="error",
                errors=[f"No pending escalation found for request_id {request_id}"],
            )

        request_fsm, combined, agent_results = self._pending_requests.pop(request_id)

        decision_result = self.escalation_agent.resolve_decision(
            request_id,
            human_decision=human_decision,
        )
        agent_results.append(decision_result)

        if not decision_result.success:
            self._pending_requests[request_id] = (
                request_fsm,
                combined,
                agent_results[:-1],
            )
            return PipelineResult(
                request_id=request_id,
                status="error",
                errors=decision_result.errors,
            )

        combined.update(decision_result.data)

        try:
            state = self.safe_mode_processor.process_until_stuck(
                request_fsm,
                combined,
            )
        except AccessBlockedBySafeModeError as e:
            audit_record = self._compile_audit(
                request_id,
                combined,
                "blocked_safe_mode",
                agent_results,
            )
            # The human decision was valid; only the system is unhealthy.
            # Keep the request pending (FSM is still in MANAGER_REVIEW) so
            # the same request can be resolved once safe mode ends.
            self._pending_requests[request_id] = (request_fsm, combined, agent_results[:-1])
            return PipelineResult(
                request_id=request_id,
                status="blocked_safe_mode",
                fsm_state="blocked_safe_mode",
                audit_record=audit_record,
                errors=[str(e)],
            )

        if state not in TERMINAL_REQUEST_STATES:
            # Day 68 fix: no decision and no timeout yet leaves the FSM
            # in MANAGER_REVIEW. That is "still pending", not "denied" -
            # keep the request parked instead of losing it.
            self._pending_requests[request_id] = (request_fsm, combined, agent_results[:-1])
            return PipelineResult(
                request_id=request_id,
                status="error",
                fsm_state=state.value,
                errors=[f"Request {request_id} is still awaiting a decision (state: {state.value})"],
            )

        audit_record = self._compile_audit(
            request_id,
            combined,
            state.value,
            agent_results,
        )
        status = "granted" if state == RequestState.CLOSED else "denied"

        return PipelineResult(
            request_id=request_id,
            status=status,
            fsm_state=state.value,
            audit_record=audit_record,
        )