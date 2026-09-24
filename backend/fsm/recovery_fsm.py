# backend/fsm/recovery_fsm.py

from typing import Optional

from fsm.base_fsm import BaseFSM
from fsm.states import SystemState
from fsm.transitions import SYSTEM_TRANSITIONS


class RecoveryFSM(BaseFSM):
    """
    Tracks overall system health, independent of any single request.
    No terminal states - the whole point is that it recovers.

    Day 81: state can optionally be backed by a shared store (see
    database.recovery_state.RedisRecoveryStateStore) so multiple worker
    processes agree on whether the system is in SAFE_MODE_ACTIVE - the
    one thing Day 80's per-request locking did not cover, since the
    safe-mode gate itself needs a single, shared answer, not a locally
    cached one. With no state_store (the default, and every pre-Day-81
    test), this behaves exactly as before: a plain in-memory attribute.

    `state` is a property rather than a plain attribute specifically so
    that every read (BaseFSM.transition(), is_safe_mode(), the health
    API) transparently goes through the shared store when one is
    configured, without BaseFSM needing to know that store exists.
    """

    def __init__(
        self,
        initial_state: SystemState = SystemState.SYSTEM_NORMAL,
        state_store=None,
    ):
        self._state_store = state_store
        # Local cache/fallback: the value used when no store is
        # configured, and the default a store reports if no worker has
        # EVER written a state yet (a brand-new deployment). Set with
        # _initializing True so constructing a new instance never
        # overwrites whatever another worker may have already put in the
        # shared store - only the store's own actual first-ever write
        # should become authoritative.
        self._initializing = True
        self._local_state = initial_state
        super().__init__(transitions=SYSTEM_TRANSITIONS, initial_state=initial_state, owner_id="")
        self._initializing = False

    @property
    def state(self) -> SystemState:
        if self._state_store is None:
            return self._local_state
        return self._state_store.get(default=self._local_state)

    @state.setter
    def state(self, value: SystemState) -> None:
        self._local_state = value
        if self._state_store is not None and not self._initializing:
            self._state_store.set(value)

    def is_safe_mode(self) -> bool:
        return self.state == SystemState.SAFE_MODE_ACTIVE
