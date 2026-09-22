# backend/tests/test_distributed_lock.py

"""
Day 80: DistributedLock, against fakeredis. Two separate client objects
share one fakeredis.FakeServer(), the same way two real worker processes
would share one real Redis instance - a single FakeStrictRedis() instance
would NOT catch a bug here, since it has no separate-process semantics
to get wrong.
"""

import threading
import time

import fakeredis
import pytest

from core.distributed_lock import DistributedLock, LockAcquisitionError


@pytest.fixture
def server():
    return fakeredis.FakeServer()


def _client(server):
    return fakeredis.FakeStrictRedis(server=server, decode_responses=True)


class TestBasicAcquireRelease:

    def test_acquire_then_release_leaves_no_trace(self, server):
        lock = DistributedLock(_client(server))

        with lock.acquire("req-1"):
            pass

        assert _client(server).get("governai:lock:req-1") is None

    def test_the_same_lock_can_be_acquired_again_after_release(self, server):
        lock = DistributedLock(_client(server))

        with lock.acquire("req-1"):
            pass
        with lock.acquire("req-1"):
            pass  # would raise if the first release had failed

    def test_a_held_lock_is_released_even_if_the_body_raises(self, server):
        lock = DistributedLock(_client(server))

        with pytest.raises(RuntimeError):
            with lock.acquire("req-1"):
                raise RuntimeError("boom")

        with lock.acquire("req-1"):
            pass  # proves the first lock was released despite the exception


class TestTwoWorkersOneRedis:

    def test_a_second_worker_cannot_acquire_a_lock_the_first_still_holds(self, server):
        worker_a = DistributedLock(_client(server))
        worker_b = DistributedLock(_client(server))

        with worker_a.acquire("req-1"):
            with pytest.raises(LockAcquisitionError):
                with worker_b.acquire("req-1"):
                    pass

    def test_different_request_ids_do_not_contend(self, server):
        worker_a = DistributedLock(_client(server))
        worker_b = DistributedLock(_client(server))

        with worker_a.acquire("req-1"):
            with worker_b.acquire("req-2"):
                pass  # unrelated keys, no contention

    def test_worker_b_can_acquire_once_worker_a_releases(self, server):
        worker_a = DistributedLock(_client(server))
        worker_b = DistributedLock(_client(server))

        with worker_a.acquire("req-1"):
            pass
        with worker_b.acquire("req-1"):
            pass

    def test_one_worker_cannot_release_a_lock_it_does_not_hold(self, server):
        # worker_a's lock expired (or was never acquired by them) and
        # worker_b has since legitimately acquired the same name - worker_a
        # exiting its (already-expired) context must not delete worker_b's lock.
        worker_a = DistributedLock(_client(server), ttl_ms=50)
        worker_b = DistributedLock(_client(server))

        with worker_a.acquire("req-1"):
            time.sleep(0.1)  # let worker_a's lock expire on its own
            with worker_b.acquire("req-1"):
                pass  # worker_a's exit (about to happen) must not steal this

        assert _client(server).get("governai:lock:req-1") is None  # worker_b's own release did clean up


class TestExpiry:

    def test_an_expired_lock_can_be_acquired_by_someone_else(self, server):
        lock = DistributedLock(_client(server), ttl_ms=50)

        with lock.acquire("req-1"):
            time.sleep(0.1)

        with DistributedLock(_client(server)).acquire("req-1"):
            pass


class TestRealConcurrency:

    def test_ten_threads_racing_for_one_name_exactly_one_wins_at_a_time(self, server):
        successes = []
        failures = []
        barrier = threading.Barrier(10)

        def attempt(worker_id):
            barrier.wait()
            lock = DistributedLock(_client(server))
            try:
                with lock.acquire("req-race"):
                    time.sleep(0.02)  # widen the window so a real double-acquire would show up
                    successes.append(worker_id)
            except LockAcquisitionError:
                failures.append(worker_id)

        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(successes) == 1
        assert len(failures) == 9

    def test_ten_threads_on_ten_different_names_all_succeed(self, server):
        results = []

        def attempt(worker_id):
            with DistributedLock(_client(server)).acquire(f"req-{worker_id}"):
                results.append(worker_id)

        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert sorted(results) == list(range(10))
