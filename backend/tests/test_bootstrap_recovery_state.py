# backend/tests/test_bootstrap_recovery_state.py

"""
Day 81: the real deliverable, end to end. Two SEPARATE build_app()
instances (standing in for two uvicorn workers), sharing one Redis and
one database, driven purely through the real HTTP API. One instance's
admin evaluates health and drives the system into safe mode; the OTHER
instance - which never called evaluate itself - must immediately see and
enforce it. Before Day 81, this test would have failed: each app's
RecoveryFSM lived only in its own process's memory.
"""

import fakeredis
from fastapi.testclient import TestClient

from agents.recovery_agent import FailureRecoveryAgent, HealthCheckResult
from api.bootstrap import build_app
from auth.passwords import PasswordService
from core.config import Settings

FAST = PasswordService(time_cost=1, memory_cost=8, parallelism=1)
DEMO_PASSWORD = "Demo-Passw0rd-Change-Me"


def _settings(db_path) -> Settings:
    return Settings(_env_file=None, database_url=f"sqlite:///{db_path}", jwt_secret_key="k" * 40)


def _redis(server):
    return fakeredis.FakeStrictRedis(server=server, decode_responses=True)


def _token(client, user_id="user-007"):
    return client.post("/auth/login", json={"user_id": user_id, "password": DEMO_PASSWORD}).json()["access_token"]


def _auth(client, user_id="user-007"):
    return {"Authorization": f"Bearer {_token(client, user_id)}"}


class TestSafeModeIsSharedAcrossTwoIndependentApps:

    def test_worker_b_blocks_requests_the_instant_worker_a_declares_safe_mode(self, tmp_path):
        db_path = tmp_path / "shared_safe_mode.db"
        redis_server = fakeredis.FakeServer()

        worker_a = TestClient(build_app(_settings(db_path), passwords=FAST, redis_client=_redis(redis_server)))
        worker_b = TestClient(build_app(_settings(db_path), passwords=FAST, redis_client=_redis(redis_server)))

        # Sanity: before anything, worker_b serves requests normally.
        before = worker_b.post("/requests", json={"resource_id": "resource-001"}, headers=_auth(worker_b))
        assert before.status_code == 200 and before.json()["status"] == "granted"

        # worker_a's admin drives the SHARED system into safe mode - two
        # evaluate calls, matching the real two-step rulebook (NORMAL ->
        # DEGRADED_WARNING -> SAFE_MODE_ACTIVE). This app's health checks
        # are the real ones (DB/agents/FSM integrity, all genuinely
        # healthy here) - so instead we reach in and force a failing
        # check onto worker_a's own recovery_agent for this one test,
        # which is the same technique Day 69's health tests already use.
        worker_a.app.state.recovery_agent = FailureRecoveryAgent(
            checks=[lambda: HealthCheckResult(check_name="fsm_integrity", healthy=False, detail="forced")],
            recovery_fsm=worker_a.app.state.pipeline.recovery_fsm,
            distributed_lock=worker_a.app.state.pipeline._distributed_lock,
        )
        worker_a.post("/system/health/evaluate", headers=_auth(worker_a))
        worker_a.post("/system/health/evaluate", headers=_auth(worker_a))
        assert worker_a.get("/system/health", headers=_auth(worker_a)).json()["system_state"] == "safe_mode_active"

        # worker_b never called evaluate - it only shares Redis and the DB.
        after = worker_b.post("/requests", json={"resource_id": "resource-001"}, headers=_auth(worker_b, "user-001"))

        assert after.status_code == 503
        assert after.json()["status"] == "blocked_safe_mode"
        assert worker_b.get("/system/health", headers=_auth(worker_b)).json()["system_state"] == "safe_mode_active"

    def test_recovery_declared_by_one_worker_is_also_seen_by_the_other(self, tmp_path):
        db_path = tmp_path / "shared_recovery.db"
        redis_server = fakeredis.FakeServer()

        worker_a = TestClient(build_app(_settings(db_path), passwords=FAST, redis_client=_redis(redis_server)))
        worker_b = TestClient(build_app(_settings(db_path), passwords=FAST, redis_client=_redis(redis_server)))

        worker_a.app.state.recovery_agent = FailureRecoveryAgent(
            checks=[lambda: HealthCheckResult(check_name="fsm_integrity", healthy=False, detail="forced")],
            recovery_fsm=worker_a.app.state.pipeline.recovery_fsm,
            distributed_lock=worker_a.app.state.pipeline._distributed_lock,
        )
        worker_a.post("/system/health/evaluate", headers=_auth(worker_a))
        worker_a.post("/system/health/evaluate", headers=_auth(worker_a))
        assert worker_b.post(
            "/requests", json={"resource_id": "resource-001"}, headers=_auth(worker_b, "user-002")
        ).status_code == 503

        # Now worker_a's checks return healthy again, and it evaluates
        # twice (SAFE_MODE_ACTIVE -> RESTORING -> SYSTEM_NORMAL).
        worker_a.app.state.recovery_agent = FailureRecoveryAgent(
            recovery_fsm=worker_a.app.state.pipeline.recovery_fsm,
            distributed_lock=worker_a.app.state.pipeline._distributed_lock,
        )
        worker_a.post("/system/health/evaluate", headers=_auth(worker_a))
        worker_a.post("/system/health/evaluate", headers=_auth(worker_a))

        recovered = worker_b.post(
            "/requests", json={"resource_id": "resource-001"}, headers=_auth(worker_b, "user-003")
        )
        assert recovered.status_code == 200
        assert recovered.json()["status"] == "granted"
