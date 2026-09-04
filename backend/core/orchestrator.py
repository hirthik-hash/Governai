# backend/core/orchestrator.py

from fsm.governance_fsm import GovernanceFSM
from fsm.recovery_fsm import RecoveryFSM
from fsm.states import RequestState


class AccessBlockedBySafeModeError(Exception):
    """Raised when a request would be granted access while the system is in safe mode."""
    pass


class SystemAwareRequestProcessor:
    """
    Coordinates a single request's GovernanceFSM against the shared
    RecoveryFSM's health state. Does not merge the two FSMs - it
    simply refuses to let a request reach ACCESS_GRANTED while the
    system is in SAFE_MODE_ACTIVE, unless the request is flagged as
    public/read-only.
    """

    def __init__(self, recovery_fsm: RecoveryFSM):
        self.recovery_fsm = recovery_fsm

    def process(self, request_fsm: GovernanceFSM, context: dict) -> RequestState:
        """
        Advances the request FSM one step, but blocks the transition
        into ACCESS_GRANTED if the system is in safe mode and the
        request is not public/read-only.
        """
        if request_fsm.state == RequestState.AUTHORIZED and self.recovery_fsm.is_safe_mode():
            if not context.get("is_public_readonly", False):
                raise AccessBlockedBySafeModeError(
                    f"Request {request_fsm.request_id} would be granted access, "
                    f"but system is in SAFE_MODE_ACTIVE and request is not "
                    f"public/read-only. Access blocked, request queued."
                )

        return request_fsm.transition(context)

    def process_until_stuck(self, request_fsm: GovernanceFSM, context: dict) -> RequestState:
        """
        Like GovernanceFSM.run_until_stuck, but routes every step
        through the safe-mode check.
        """
        from fsm.states import TERMINAL_REQUEST_STATES

        while (
            request_fsm.state not in TERMINAL_REQUEST_STATES
            and request_fsm.can_transition(context)
        ):
            self.process(request_fsm, context)
        return request_fsm.state