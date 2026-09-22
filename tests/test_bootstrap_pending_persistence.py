# backend/tests/test_bootstrap_pending_persistence.py

"""Day 79: a pending escalation survives a real restart, through the actual HTTP API."""

import fakeredis
from fastapi.testclient import TestClient

from api.bootstrap import build_app
from auth.passwords import PasswordService
from core.config import Settings

FAST = PasswordService(time_cost=1, memory_cost=8, parallelism=1)
DEMO_PASSWORD = "Demo-Passw0rd-Change-Me"


def _redis(server):
    return fakeredis.FakeStrictRedis(server=server, decode_responses=True)


def _settings(db_path) -> Settings:
    return Settings(_env_file=None, database_url=f"sqlite:///{db_path}", jwt_secret_key="k" * 40)


def _token(client, user_id):
    return client.post("/auth/login", json={"user_id": user_id, "password": DEMO_PASSWORD}).json()["access_token"]


def _auth(client, user_id):
    return {"Authorization": f"Bearer {_token(client, user_id)}"}


class TestPendingEscalationSurvivesARestart:

    def test_a_different_process_can_see_and_approve_it(self, tmp_path):
        db_path = tmp_path / "pending_restart.db"
        settings = _settings(db_path)
        redis_server = fakeredis.FakeServer()

        first_client = TestClient(build_app(settings, passwords=FAST, redis_client=_redis(redis_server)))
        # user-001 (clearance 1) -> resource-003 (restricted, clearance 3) escalates to user-002.
        submitted = first_client.post(
            "/requests", json={"resource_id": "resource-003", "request_id": "req-pending-restart-1"},
            headers=_auth(first_client, "user-001"),
        )
        assert submitted.status_code == 202
        approver_id = submitted.json()["notification"]["approver_user_id"]

        # A fresh process: new engine, new pipeline, same database file
        # (and the same shared Redis, as two real worker processes would have).
        second_client = TestClient(build_app(settings, passwords=FAST, redis_client=_redis(redis_server)))

        pending = second_client.get("/requests/pending", headers=_auth(second_client, approver_id)).json()
        assert pending["count"] == 1
        assert pending["pending"][0]["request_id"] == "req-pending-restart-1"

        resolved = second_client.post(
            "/requests/req-pending-restart-1/resolve", json={"decision": "approved"},
            headers=_auth(second_client, approver_id),
        )
        assert resolved.status_code == 200
        assert resolved.json()["status"] == "granted"

    def test_the_original_requester_cannot_approve_it_even_after_the_restart(self, tmp_path):
        db_path = tmp_path / "pending_restart2.db"
        settings = _settings(db_path)
        redis_server = fakeredis.FakeServer()

        first_client = TestClient(build_app(settings, passwords=FAST, redis_client=_redis(redis_server)))
        first_client.post(
            "/requests", json={"resource_id": "resource-003", "request_id": "req-pending-restart-2"},
            headers=_auth(first_client, "user-001"),
        )

        second_client = TestClient(build_app(settings, passwords=FAST, redis_client=_redis(redis_server)))
        response = second_client.post(
            "/requests/req-pending-restart-2/resolve", json={"decision": "approved"},
            headers=_auth(second_client, "user-001"),
        )

        assert response.status_code == 404  # RBAC (Day 77) survives the restart too
