# backend/fsm/recovery_fsm.py

from fsm.base_fsm import BaseFSM
from fsm.states import SystemState
from fsm.transitions import SYSTEM_TRANSITIONS


class RecoveryFSM(BaseFSM):
    """
    Tracks overall system health, independent of any single request.
    No terminal states - the whole point is that it recovers.
    """

    def __init__(self, initial_state: SystemState = SystemState.SYSTEM_NORMAL):
        super().__init__(transitions=SYSTEM_TRANSITIONS, initial_state=initial_state, owner_id="")

    def is_safe_mode(self) -> bool:
        return self.state == SystemState.SAFE_MODE_ACTIVE