# backend/database/recovery_state.py

"""
Redis-backed shared system-health state (Day 81).

Until now, RecoveryFSM.state lived only in one process's memory - two
uvicorn workers (or two app instances behind a load balancer) each had
their OWN idea of whether the system was in SAFE_MODE_ACTIVE. That is
the one gap Day 80's request-locking did not touch: the safe-mode GATE
itself (SystemAwareRequestProcessor.process(), checked on every single
request) could give a different answer depending on which worker
happened to handle the request. RedisRecoveryStateStore makes all
workers sharing one Redis agree on the same system state.

Fails LOUD on every read and write, deliberately: a governance system
that cannot verify its own safety state must not silently assume
"probably fine" and let requests through. See RecoveryStateUnavailableError.
"""

from typing import Optional

import redis

from fsm.states import SystemState

DEFAULT_STATE_KEY = "governai:recovery:state"


class RecoveryStateUnavailableError(Exception):
    """Redis could not be reached (or returned something unrecognizable) while reading/writing system state."""


class RedisRecoveryStateStore:

    def __init__(self, redis_client: redis.Redis, key: str = DEFAULT_STATE_KEY):
        self._redis = redis_client
        self._key = key

    def get(self, default: SystemState) -> SystemState:
        """
        The shared state, or `default` if no worker has ever written one
        yet (a brand-new deployment). Raises RecoveryStateUnavailableError
        if Redis itself cannot be reached - NOT silently `default` in that
        case, since that would look identical to "no one has ever seen a
        problem" when the true answer might be "we cannot tell."
        """
        try:
            raw = self._redis.get(self._key)
        except redis.RedisError as error:
            raise RecoveryStateUnavailableError(f"Cannot read system state from Redis: {error}") from error

        if raw is None:
            return default
        try:
            return SystemState(raw)
        except ValueError as error:
            raise RecoveryStateUnavailableError(f"Redis holds an unrecognized system state: {raw!r}") from error

    def set(self, state: SystemState) -> None:
        try:
            self._redis.set(self._key, state.value)
        except redis.RedisError as error:
            raise RecoveryStateUnavailableError(f"Cannot write system state to Redis: {error}") from error
