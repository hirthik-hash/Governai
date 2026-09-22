# backend/tests/test_bootstrap_persistence.py

"""Day 78: build_app() persists the ledger and survives a restart against the same database file."""

import fakeredis
import pytest
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


def _token(client, user_id="user-007"):
    return client.post("/auth/login", json={"user_id": user_id, "password": DEMO_PASSWORD}).json()["access_token"]


class TestAuditLedgerSurvivesARestart:

    def test_a_granted_request_is_visible_to_a_second_process(self, tmp_path):
        db_path = tmp_path / "restart.db"
        settings = _settings(db_path)
        redis_server = fakeredis.FakeServer()

        first_app = build_app(settings, passwords=FAST, redis_client=_redis(redis_server))
        first_client = TestClient(first_app)
        first_client.post("/requests", json={"resource_id": "resource-001", "request_id": "req-restart-1"},
                          headers={"Authorization": f"Bearer {_token(first_client)}"})

        # A fresh process: new engine, new pipeline, same database file
        # (and the same shared Redis, as two real worker processes would have).
        second_client = TestClient(build_app(settings, passwords=FAST, redis_client=_redis(redis_server)))
        admin_token = _token(second_client)

        body = second_client.get("/audit", headers={"Authorization": f"Bearer {admin_token}"}).json()

        assert body["count"] == 1
        assert body["records"][0]["request_id"] == "req-restart-1"

    def test_the_same_request_id_is_refused_after_a_restart(self, tmp_path):
        db_path = tmp_path / "restart2.db"
        settings = _settings(db_path)
        redis_server = fakeredis.FakeServer()

        first_client = TestClient(build_app(settings, passwords=FAST, redis_client=_redis(redis_server)))
        first_client.post("/requests", json={"resource_id": "resource-001", "request_id": "req-restart-2"},
                          headers={"Authorization": f"Bearer {_token(first_client)}"})

        second_client = TestClient(build_app(settings, passwords=FAST, redis_client=_redis(redis_server)))
        response = second_client.post("/requests", json={"resource_id": "resource-001", "request_id": "req-restart-2"},
                                      headers={"Authorization": f"Bearer {_token(second_client)}"})

        assert response.status_code == 400
        assert "already been finalized" in response.json()["errors"][0]
