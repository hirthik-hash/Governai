# backend/fsm/governance_fsm.py

from fsm.base_fsm import BaseFSM
from fsm.exceptions import InvalidTransitionError, AmbiguousTransitionError, TerminalStateError
from fsm.states import RequestState, TERMINAL_REQUEST_STATES
from fsm.transitions import TRANSITIONS

# Re-exported so existing imports like
# `from fsm.governance_fsm import TerminalStateError` keep working.
__all__ = [
    "GovernanceFSM",
    "InvalidTransitionError",
    "AmbiguousTransitionError",
    "TerminalStateError",
]


class GovernanceFSM(BaseFSM):
    """
    Drives a single access request through its lifecycle using the
    rulebook defined in transitions.py.
    """

    def __init__(self, request_id: str, initial_state: RequestState = RequestState.IDLE):
        super().__init__(transitions=TRANSITIONS, initial_state=initial_state, owner_id=request_id)
        self.request_id = request_id

    def _is_terminal(self, state: RequestState) -> bool:
        return state in TERMINAL_REQUEST_STATES