# backend/tests/test_bootstrap.py

"""Day 76: api.bootstrap.build_app() - the real wiring from Settings."""

import pytest
from fastapi.testclient import TestClient

from api.bootstrap import build_app
from auth.passwords import PasswordService
from core.config import DEFAULT_JWT_SECRET, Settings

FAST = PasswordService(time_cost=1, memory_cost=8, parallelism=1)
PRIVATE_SECRET = "p" * 48


def _settings(**overrides) -> Settings:
    # _env_file=None: never read a developer's real .env during tests.
    fields = dict(database_url="sqlite://", jwt_secret_key=PRIVATE_SECRET)
    fields.update(overrides)
    return Settings(_env_file=None, **fields)


@pytest.fixture(scope="module")
def dev_client():
    return TestClient(build_app(_settings(), passwords=FAST))


class TestDevelopmentBuild:

    def test_demo_users_can_log_in_with_the_demo_password(self, dev_client):
        response = dev_client.post("/auth/login", json={"user_id": "user-007", "password": "Demo-Passw0rd-Change-Me"})

        assert response.status_code == 200

    def test_a_demo_only_user_exists_beyond_the_original_seed(self, dev_client):
        token = dev_client.post("/auth/login", json={"user_id": "user-017", "password": "Demo-Passw0rd-Change-Me"}).json()["access_token"]

        me = dev_client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()

        assert me["name"] == "Hannah Berg"

    def test_wrong_password_is_refused(self, dev_client):
        assert dev_client.post("/auth/login", json={"user_id": "user-007", "password": "not-the-password"}).status_code == 401

    def test_the_ciso_is_the_only_admin(self, dev_client):
        def api_role(user_id):
            token = dev_client.post("/auth/login", json={"user_id": user_id, "password": "Demo-Passw0rd-Change-Me"}).json()["access_token"]
            return dev_client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["api_role"]

        assert api_role("user-007") == "admin"
        assert api_role("user-001") == "user"
        assert api_role("user-017") == "user"

    def test_the_pipeline_reads_users_and_resources_from_the_database_and_verifies_the_session(self, dev_client):
        # user-017 and resource-018 exist only in the demo dataset, not the seed data.
        token = dev_client.post("/auth/login", json={"user_id": "user-017", "password": "Demo-Passw0rd-Change-Me"}).json()["access_token"]

        response = dev_client.post("/requests", json={"resource_id": "resource-018"}, headers={"Authorization": f"Bearer {token}"})

        assert response.json()["status"] == "granted"

    def test_requests_need_a_token(self, dev_client):
        assert dev_client.post("/requests", json={"resource_id": "resource-018"}).status_code == 401

    def test_the_admin_only_routes_are_protected_in_the_real_wiring(self, dev_client):
        def token(user_id):
            return dev_client.post("/auth/login", json={"user_id": user_id, "password": "Demo-Passw0rd-Change-Me"}).json()["access_token"]

        assert dev_client.get("/audit", headers={"Authorization": f"Bearer {token('user-001')}"}).status_code == 403
        assert dev_client.get("/audit", headers={"Authorization": f"Bearer {token('user-007')}"}).status_code == 200

    def test_custom_demo_password_is_honored(self):
        client = TestClient(build_app(_settings(demo_user_password="Another-Demo-Passw0rd"), passwords=FAST))

        assert client.post("/auth/login", json={"user_id": "user-001", "password": "Another-Demo-Passw0rd"}).status_code == 200
        assert client.post("/auth/login", json={"user_id": "user-001", "password": "Demo-Passw0rd-Change-Me"}).status_code == 401


class TestProductionBuild:

    def test_refuses_to_start_with_the_public_default_secret(self):
        with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
            build_app(_settings(app_env="production", jwt_secret_key=DEFAULT_JWT_SECRET), passwords=FAST)

    def test_refuses_a_short_secret_in_any_mode(self):
        with pytest.raises(ValueError, match="at least 32"):
            build_app(_settings(jwt_secret_key="change-me"), passwords=FAST)

    def test_production_seeds_no_demo_users_or_passwords(self):
        client = TestClient(build_app(_settings(app_env="production"), passwords=FAST))

        assert client.post("/auth/login", json={"user_id": "user-007", "password": "Demo-Passw0rd-Change-Me"}).status_code == 401
        assert client.post("/requests", json={"resource_id": "resource-001"}).status_code == 401  # nobody can hold a token
