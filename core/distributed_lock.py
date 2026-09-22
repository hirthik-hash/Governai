# backend/core/distributed_lock.py

"""
A short-lived, single-Redis-instance mutual-exclusion lock (Day 80).

Built directly on SET NX PX (acquire) and a WATCH/MULTI compare-and-
delete (release) rather than a Lua/EVAL script: fakeredis's scripting
support is not something this project wants to depend on in tests (see
the project's own "fake external dependencies" testing convention), and
these two primitives are enough to do this safely without it.

This is NOT a Redlock implementation for multiple independent Redis
nodes - the docker-compose setup here runs one Redis instance, and that
is the scope this lock is built for: keeping GovernAI's own worker
processes from racing each other, not surviving a Redis node failure
mid-lock. A future move to a Redis cluster would need a real Redlock
(or a managed distributed-lock service) instead.

Acquisition is non-blocking and fails immediately if the lock is held -
deliberately, so a caller under load gets a fast, clear error rather
than piling up waiting threads. The TTL is the backstop against a
worker crashing mid-lock: even with no release, the lock expires on its
own and is never held forever.
"""

import uuid
from contextlib import contextmanager
from typing import Iterator

import redis

DEFAULT_TTL_MS = 10_000
DEFAULT_KEY_PREFIX = "governai:lock:"


class LockAcquisitionError(Exception):
    """Raised when a lock is already held by someone else."""


class DistributedLock:

    def __init__(self, redis_client: redis.Redis, key_prefix: str = DEFAULT_KEY_PREFIX, ttl_ms: int = DEFAULT_TTL_MS):
        self._redis = redis_client
        self._key_prefix = key_prefix
        self._ttl_ms = ttl_ms

    @contextmanager
    def acquire(self, name: str) -> Iterator[None]:
        key = f"{self._key_prefix}{name}"
        token = uuid.uuid4().hex

        if not self._redis.set(key, token, nx=True, px=self._ttl_ms):
            raise LockAcquisitionError(f"Lock '{name}' is already held by another process")

        try:
            yield
        finally:
            self._release(key, token)

    def _release(self, key: str, token: str) -> None:
        """
        Only deletes the key if it still holds OUR token - otherwise this
        release could delete a lock some other process has since
        acquired (e.g. ours expired under heavy load and someone else
        already grabbed it). If that race is lost, the TTL is still the
        backstop: the other holder's own lock still expires on its own
        schedule, exactly as if we had never touched it.
        """
        with self._redis.pipeline() as pipe:
            try:
                pipe.watch(key)
                if pipe.get(key) == token:
                    pipe.multi()
                    pipe.delete(key)
                    pipe.execute()
                else:
                    pipe.unwatch()
            except redis.WatchError:
                pass  # someone else touched the key between our GET and DELETE; leave it alone
