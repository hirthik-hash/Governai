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
from core.distributed_lock import DistributedLock, LockAcquisitionError
from data.directory import Directory, SeedDirectory
from fsm.recovery_fsm import RecoveryFSM
from fsm.transitions import manager_approved
from core.decision_logger import DecisionLogger
from core.request_history_tracker import RequestHistoryTracker
from core.geo_anomaly_detector import GeoAnomalyDetector
from core.timeout_tracker import EscalationTimeoutTracker
from agents.request_agent import RequestUnderstandingAgent
from agents.validation_agent import AccessValidationAgent, SessionValidator
from agents.security_agent import SecurityRiskAgent
from agents.escalation_agent import EscalationAgent, NotificationRecord
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
        directory: Directory = None,
        session_validator: SessionValidator = None,
        audit_store=None,
        pending_store=None,
        distributed_lock: DistributedLock = None,
    ):
        # One Directory shared by every agent that looks up users/resources
        # (Day 75): seed data by default, or a DatabaseDirectory.
        self.directory = directory or SeedDirectory()
        self.request_agent = RequestUnderstandingAgent(directory=self.directory)
        # Day 77: the API passes a JwtSessionValidator; everything else keeps
        # the default placeholder check.
        self.validation_agent = AccessValidationAgent(session_validator=session_validator)
        self.security_agent = SecurityRiskAgent(
            history_tracker=history_tracker or RequestHistoryTracker(),
            geo_detector=geo_detector or GeoAnomalyDetector(),
        )
        self.escalation_agent = EscalationAgent(
            timeout_tracker=timeout_tracker or EscalationTimeoutTracker(),
            directory=self.directory,
        )
        self.audit_agent = AuditComplianceAgent()

        self.recovery_fsm = recovery_fsm or RecoveryFSM()
        self.decision_logger = decision_logger or DecisionLogger()
        self.safe_mode_processor = SystemAwareRequestProcessor(self.recovery_fsm, self.decision_logger)

        # In-memory pending-requests store: request_id -> (GovernanceFSM, context dict, [AgentResult, ...])
        self._pending_requests: dict = {}

        # Every AuditRecord compiled by this pipeline, in order (Day 70) -
        # the fast in-process read path used by the audit routes. A
        # request can appear more than once (e.g. a safe-mode-blocked
        # PENDING record, then its final one).
        self.audit_records: list = []

        # Day 78: optional durability layer (database.audit_store.DatabaseAuditStore).
        # None (the default, and every test's default) means audit records
        # live only in self.audit_records, exactly as before Day 78.
        self._audit_store = audit_store
        # request_ids that have received a GRANTED or DENIED record THIS
        # process - checked before re-running any agent, so a replayed id
        # never produces a second final decision even without a database
        # configured. When an audit_store IS configured, has_final_record()
        # is also checked, so a replay is caught even from a fresh pipeline
        # instance after a restart.
        self._finalized_request_ids: set = set()

        # Day 79: optional durability layer (database.pending_store.
        # DatabasePendingEscalationStore) for escalations parked awaiting
        # a human decision. None (the default, and every test's default)
        # means pending state lives only in self._pending_requests, exactly
        # as before Day 79. When configured, every park/unpark below is
        # mirrored to the database, and any escalations left pending from
        # a previous run are restored into memory right now, below.
        self._pending_store = pending_store
        if self._pending_store is not None:
            self._restore_pending_from_store()

        # Day 80: optional cross-process lock (core.distributed_lock.
        # DistributedLock, backed by Redis). None (the default, and every
        # test before Day 80) means submit_request()/resolve_escalation()
        # run exactly as before - single-process safety only.
        self._distributed_lock = distributed_lock

    def submit_request(self, request_data: dict) -> PipelineResult:
        """
        Public entry point. Day 80: when a distributed_lock is configured,
        every request_id is processed under a short-lived Redis lock, so
        two worker PROCESSES can never both decide the same NEW
        request_id at once - the exact race Day 79 left as a documented,
        unsolved gap for a multi-worker deployment. With no lock
        configured (the default, and every pre-Day-80 test), behavior is
        identical to before this method existed.
        """
        request_id = request_data.get("request_id", f"req-{id(request_data)}")
        if self._distributed_lock is None:
            return self._submit_request_locked(request_id, request_data)
        try:
            with self._distributed_lock.acquire(f"submit:{request_id}"):
                return self._submit_request_locked(request_id, request_data)
        except LockAcquisitionError:
            return PipelineResult(
                request_id=request_id,
                status="error",
                errors=[f"Request {request_id} is already being processed by another worker"],
            )

    def _submit_request_locked(self, request_id: str, request_data: dict) -> PipelineResult:
        # Day 70: fail loud instead of silently overwriting a parked
        # escalation. Checked before any agent runs, so a rejected
        # duplicate has no side effects (no history, geo or timeout
        # state is touched). Duplicate ids of COMPLETED requests are
        # not detected here - that becomes a DB unique constraint.
        if request_id in self._pending_requests:
            return PipelineResult(
                request_id=request_id,
                status="error",
                errors=[f"Duplicate request_id {request_id}: an escalation with this id is already pending"],
            )

        # Day 78: a request_id that already has a final (GRANTED/DENIED)
        # record - in this process, or (if audit_store is configured) in
        # the database from a previous run - is refused rather than
        # re-decided. Checked before any agent runs, exactly like the
        # pending-duplicate check above, and for the same reason: a
        # replay must have no side effects, not just a matching answer.
        if request_id in self._finalized_request_ids or (
            self._audit_store is not None and self._audit_store.has_final_record(request_id)
        ):
            return PipelineResult(
                request_id=request_id,
                status="error",
                errors=[f"Duplicate request_id {request_id}: this request has already been finalized"],
            )

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

            self._park(request_id, request_fsm, combined, agent_results)

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
        audit_record = audit_result.data.get("audit_record") if audit_result.success else None
        if audit_record is not None:
            self.audit_records.append(audit_record)
            if audit_record.final_decision in ("GRANTED", "DENIED"):
                self._finalized_request_ids.add(audit_record.request_id)
            if self._audit_store is not None:
                self._audit_store.add(audit_record)
        return audit_record

    def _park(self, request_id: str, request_fsm: GovernanceFSM, combined: dict, agent_results: list) -> None:
        """The single place a request is parked as pending - keeps the in-memory dict and the optional database store in sync."""
        self._pending_requests[request_id] = (request_fsm, combined, agent_results)
        if self._pending_store is not None:
            tracker = self.escalation_agent.timeout_tracker
            self._pending_store.save(
                request_id, request_fsm.state.value, combined, agent_results,
                tracker.sent_at(request_id), tracker.timeout_seconds_for(request_id),
            )

    def _unpark(self, request_id: str) -> tuple:
        """The single place a pending request is removed - keeps the in-memory dict and the optional database store in sync."""
        entry = self._pending_requests.pop(request_id)
        if self._pending_store is not None:
            self._pending_store.delete(request_id)
        return entry

    def _restore_pending_from_store(self) -> None:
        """
        Runs once, at construction, before any request is processed:
        rebuilds every escalation left pending by a previous run into
        self._pending_requests and re-seeds the timeout tracker with each
        one's ORIGINAL sent-at time (not "now"), so a request that was
        already close to timing out before a restart still is after one.
        """
        for restored in self._pending_store.load_all():
            context = dict(restored.context)
            if context.get("notification") is not None:
                context["notification"] = NotificationRecord(**context["notification"])
            request_fsm = GovernanceFSM(request_id=restored.request_id, initial_state=RequestState(restored.fsm_state))
            self.escalation_agent.timeout_tracker.restore(
                restored.request_id, restored.timeout_sent_at, restored.timeout_seconds
            )
            self._pending_requests[restored.request_id] = (request_fsm, context, list(restored.agent_results))

    def has_pending_request(self, request_id: str) -> bool:
        return request_id in self._pending_requests

    def get_pending_notification(self, request_id: str):
        """The NotificationRecord of one pending escalation, or None."""
        pending = self._pending_requests.get(request_id)
        if pending is None:
            return None
        return pending[1].get("notification")

    def list_pending_notifications(self) -> list:
        """
        The NotificationRecord of every escalation still awaiting a
        decision, in submission order. Public read access so callers
        (the API) never reach into _pending_requests directly.
        """
        return [
            combined["notification"]
            for _fsm, combined, _results in self._pending_requests.values()
            if combined.get("notification") is not None
        ]

    def resolve_escalation(self, request_id: str, human_decision: str = None) -> PipelineResult:
        """
        Completes a request previously parked by submit_request()
        when it reached ESCALATION_REQUIRED. human_decision is
        "approved", "rejected", or None (checks the timeout tracker
        instead) - see EscalationAgent.resolve_decision() (Day 45)
        for the exact semantics, including the Day 46 policy that a
        late human decision always overrides an expired timeout.

        Day 80: locked the same way as submit_request(), so two workers
        cannot both resolve the same escalation at once (e.g. two admins
        clicking approve within milliseconds of each other, landing on
        different processes).
        """
        if self._distributed_lock is None:
            return self._resolve_escalation_locked(request_id, human_decision)
        try:
            with self._distributed_lock.acquire(f"resolve:{request_id}"):
                return self._resolve_escalation_locked(request_id, human_decision)
        except LockAcquisitionError:
            return PipelineResult(
                request_id=request_id,
                status="error",
                errors=[f"Request {request_id} is already being resolved by another worker"],
            )

    def _resolve_escalation_locked(self, request_id: str, human_decision: str = None) -> PipelineResult:
        # Day 80: a genuine multi-worker gap, found by testing (not by
        # inspection) - each RequestPipeline restores pending escalations
        # into its OWN in-memory dict once, at construction (Day 79). If
        # worker A resolves request_id and worker B was never told (no
        # process restart, just two long-running workers), worker B's
        # in-memory copy is STALE: it still shows the request as pending
        # even after worker A has finalized it in the database. The lock
        # above only prevents worker A and B from resolving it AT THE
        # SAME INSTANT - it does nothing to stop a later, purely
        # sequential second resolution by a worker with stale state. This
        # check closes that: the same guard submit_request() already had
        # since Day 78, now applied here too.
        if request_id in self._finalized_request_ids or (
            self._audit_store is not None and self._audit_store.has_final_record(request_id)
        ):
            return PipelineResult(
                request_id=request_id,
                status="error",
                errors=[f"Request {request_id} has already been finalized"],
            )

        if request_id not in self._pending_requests:
            return PipelineResult(
                request_id=request_id,
                status="error",
                errors=[f"No pending escalation found for request_id {request_id}"],
            )

        request_fsm, combined, agent_results = self._unpark(request_id)

        decision_result = self.escalation_agent.resolve_decision(
            request_id,
            human_decision=human_decision,
        )
        agent_results.append(decision_result)

        if not decision_result.success:
            self._park(request_id, request_fsm, combined, agent_results[:-1])
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
            self._park(request_id, request_fsm, combined, agent_results[:-1])
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
            self._park(request_id, request_fsm, combined, agent_results[:-1])
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