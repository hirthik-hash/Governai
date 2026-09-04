# backend/core/orchestrator.py — updated

from fsm.governance_fsm import GovernanceFSM
from fsm.recovery_fsm import RecoveryFSM
from fsm.states import RequestState, TERMINAL_REQUEST_STATES
from core.decision_logger import DecisionLogger


class AccessBlockedBySafeModeError(Exception):
    pass


class SystemAwareRequestProcessor:

    def __init__(self, recovery_fsm: RecoveryFSM, logger: DecisionLogger | None = None):
        self.recovery_fsm = recovery_fsm
        self.logger = logger or DecisionLogger()

    def process(self, request_fsm: GovernanceFSM, context: dict) -> RequestState:
        if request_fsm.state == RequestState.AUTHORIZED and self.recovery_fsm.is_safe_mode():
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
    # backend/core/orchestrator.py — add this method to SystemAwareRequestProcessor

    def transition_system(self, context: dict):
        """
        Advances the shared RecoveryFSM and logs the resulting
        transition, the same way process() does for request FSMs.
        Use this instead of calling recovery_fsm.transition()
        directly, so system health changes are never invisible to
        the audit trail.
        """
        new_state = self.recovery_fsm.transition(context)
        self.logger.log_system_transition(self.recovery_fsm.history[-1])
        return new_state