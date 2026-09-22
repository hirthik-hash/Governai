# backend/tests/test_orchestrator_distributed_lock.py

"""
Day 80: RequestPipeline with a DistributedLock configured.

The real target is the race Day 79 explicitly left open: two SEPARATE
RequestPipeline instances (standing in for two worker processes) sharing
one database, racing on the SAME brand-new request_id. Without a lock,
both could pass the in-memory/DB duplicate checks before either commits,
producing two final records for one request. These tests build two real
pipelines sharing one fakeredis server and one shared database, and fire
them from real threads - not a mock, an actual race.
"""

import threading

import fakeredis
import pytest

from core.distributed_lock import DistributedLock, LockAcquisitionError
from core.orchestrator import RequestPipeline
from database.audit_store import DatabaseAuditStore
from database.pending_store import DatabasePendingEscalationStore
from database.seeding import seed_database
from database.session import init_db, make_engine, make_session_factory

GRANTED = {"user_id": "user-007", "resource_id": "resource-001", "session_token": "abc"}
ESCALATING = {"user_id": "user-003", "resource_id": "resource-003", "session_token": "abc"}


@pytest.fixture
def redis_server():
    return fakeredis.FakeServer()


def _redis_client(server):
    return fakeredis.FakeStrictRedis(server=server, decode_responses=True)


@pytest.fixture
def db_factory():
    engine = make_engine("sqlite://")
    init_db(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        seed_database(session)
        session.commit()
    return factory


def _worker_pipeline(db_factory, redis_server):
    return RequestPipeline(
        audit_store=DatabaseAuditStore(db_factory),
        pending_store=DatabasePendingEscalationStore(db_factory),
        distributed_lock=DistributedLock(_redis_client(redis_server)),
    )


class TestSingleWorkerBehaviorUnchanged:

    def test_a_lock_configured_pipeline_still_grants_normally(self, db_factory, redis_server):
        result = _worker_pipeline(db_factory, redis_server).submit_request({**GRANTED, "request_id": "req-1"})

        assert result.status == "granted"

    def test_and_still_escalates_and_resolves_normally(self, db_factory, redis_server):
        pipeline = _worker_pipeline(db_factory, redis_server)
        pipeline.submit_request({**ESCALATING, "request_id": "req-2"})

        result = pipeline.resolve_escalation("req-2", human_decision="approved")

        assert result.status == "granted"

    def test_the_lock_leaves_no_key_behind_after_a_normal_request(self, db_factory, redis_server):
        client = _redis_client(redis_server)
        _worker_pipeline(db_factory, redis_server).submit_request({**GRANTED, "request_id": "req-3"})

        assert client.keys("governai:lock:*") == []


class TestTwoWorkersRacingOnTheSameNewRequestId:

    def test_exactly_one_of_two_concurrent_submits_is_processed(self, db_factory, redis_server):
        worker_a = _worker_pipeline(db_factory, redis_server)
        worker_b = _worker_pipeline(db_factory, redis_server)
        results = {}
        barrier = threading.Barrier(2)

        def run(pipeline, key):
            barrier.wait()
            results[key] = pipeline.submit_request({**GRANTED, "request_id": "req-race-1"})

        t_a = threading.Thread(target=run, args=(worker_a, "a"))
        t_b = threading.Thread(target=run, args=(worker_b, "b"))
        t_a.start(); t_b.start()
        t_a.join(); t_b.join()

        statuses = [results["a"].status, results["b"].status]
        assert statuses.count("granted") == 1
        assert statuses.count("error") == 1

    def test_the_database_ends_up_with_exactly_one_final_record_not_two(self, db_factory, redis_server):
        worker_a = _worker_pipeline(db_factory, redis_server)
        worker_b = _worker_pipeline(db_factory, redis_server)
        barrier = threading.Barrier(2)

        def run(pipeline):
            barrier.wait()
            pipeline.submit_request({**GRANTED, "request_id": "req-race-2"})

        threads = [threading.Thread(target=run, args=(w,)) for w in (worker_a, worker_b)]
        for t in threads: t.start()
        for t in threads: t.join()

        stored = DatabaseAuditStore(db_factory).all()
        assert len(stored) == 1
        assert stored[0].request_id == "req-race-2"

    def test_twenty_workers_racing_on_one_new_id_still_produce_exactly_one_record(self, db_factory, redis_server):
        workers = [_worker_pipeline(db_factory, redis_server) for _ in range(20)]
        barrier = threading.Barrier(20)
        outcomes = []
        lock = threading.Lock()

        def run(pipeline):
            barrier.wait()
            result = pipeline.submit_request({**GRANTED, "request_id": "req-race-3"})
            with lock:
                outcomes.append(result.status)

        threads = [threading.Thread(target=run, args=(w,)) for w in workers]
        for t in threads: t.start()
        for t in threads: t.join()

        assert outcomes.count("granted") == 1
        assert outcomes.count("error") == 19
        assert len(DatabaseAuditStore(db_factory).all()) == 1

    def test_losing_the_race_gives_a_clear_error_message(self, db_factory, redis_server):
        client = _redis_client(redis_server)
        worker_a = _worker_pipeline(db_factory, redis_server)
        worker_b = RequestPipeline(distributed_lock=DistributedLock(client))

        with worker_a._distributed_lock.acquire("submit:req-4"):
            result = worker_b.submit_request({**GRANTED, "request_id": "req-4"})

        assert result.status == "error"
        assert "already being processed" in result.errors[0]


class TestTwoWorkersRacingOnTheSameResolve:

    def test_only_one_of_two_concurrent_approvals_succeeds(self, db_factory, redis_server):
        # A pending_store is required here (unlike the submit-race tests
        # above): two SEPARATE pipeline instances only see the SAME
        # pending escalation via the shared database, exactly as two real
        # worker processes would - there is no other channel between them.
        setup_pipeline = _worker_pipeline(db_factory, redis_server)
        setup_pipeline.submit_request({**ESCALATING, "request_id": "req-resolve-race"})

        worker_a = _worker_pipeline(db_factory, redis_server)
        worker_b = _worker_pipeline(db_factory, redis_server)
        results = {}
        barrier = threading.Barrier(2)

        def run(pipeline, key):
            barrier.wait()
            results[key] = pipeline.resolve_escalation("req-resolve-race", human_decision="approved")

        t_a = threading.Thread(target=run, args=(worker_a, "a"))
        t_b = threading.Thread(target=run, args=(worker_b, "b"))
        t_a.start(); t_b.start()
        t_a.join(); t_b.join()

        statuses = [results["a"].status, results["b"].status]
        assert statuses.count("granted") == 1
        assert statuses.count("error") == 1
        assert len(DatabaseAuditStore(db_factory).all()) == 1


class TestNoLockConfiguredIsUnaffected:

    def test_pipelines_without_a_lock_behave_exactly_as_before_day_80(self):
        pipeline = RequestPipeline()

        result = pipeline.submit_request({**GRANTED, "request_id": "req-no-lock"})

        assert result.status == "granted"
        assert pipeline._distributed_lock is None
