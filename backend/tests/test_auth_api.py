# backend/tests/test_auth_api.py

"""Day 76: /auth/login and /auth/me over HTTP, and the current-user dependency."""

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from auth.credentials import InMemoryCredentialStore
from auth.passwords import PasswordService
from auth.service import AuthService
from auth.tokens import TokenService
from core.orchestrator import RequestPipeline
from data.directory import SeedDirectory
from data.seed_data import SEED_USERS

SECRET = "s" * 40
PASSWORD = "correct-horse-battery"
NOW = datetime(2026, 9, 22, 10, 0, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self):
        self.current = NOW

    def now(self):
        return self.current

    def advance(self, seconds):
        self.current += timedelta(seconds=seconds)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def users():
    return list(SEED_USERS)


@pytest.fixture
def client(clock, users):
    directory = SeedDirectory(users=users)
    auth = AuthService(
        credentials=InMemoryCredentialStore(),
        directory=directory,
        passwords=PasswordService(time_cost=1, memory_cost=8, parallelism=1),
        tokens=TokenService(SECRET, expiry_minutes=60, now_fn=clock.now),
    )
    auth.set_password("user-007", PASSWORD)
    auth.set_password("user-001", PASSWORD)
    return TestClient(create_app(pipeline=RequestPipeline(directory=directory), auth_service=auth))


def _login(client, user_id="user-007", password=PASSWORD):
    return client.post("/auth/login", json={"user_id": user_id, "password": password})


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


class TestLogin:

    def test_success_returns_a_bearer_token(self, client):
        response = _login(client)

        assert response.status_code == 200
        body = response.json()
        assert body["token_type"] == "bearer"
        assert body["expires_in"] == 3600
        assert jwt.get_unverified_header(body["access_token"])["alg"] == "HS256"

    @pytest.mark.parametrize("user_id,password", [
        ("user-007", "wrong-password-x"),
        ("user-999", PASSWORD),
        ("user-002", PASSWORD),  # exists, no password set
    ])
    def test_every_failure_is_the_same_401(self, client, user_id, password):
        response = _login(client, user_id, password)

        assert response.status_code == 401
        assert response.json() == {"detail": "Invalid credentials"}
        assert response.headers["www-authenticate"] == "Bearer"

    @pytest.mark.parametrize("body", [
        {}, {"user_id": "user-007"}, {"password": PASSWORD},
        {"user_id": "", "password": PASSWORD},
        {"user_id": "user-007", "password": ""},
        {"user_id": "user-007", "password": "x" * 1025},
        {"user_id": "u" * 65, "password": PASSWORD},
    ])
    def test_malformed_bodies_are_422(self, client, body):
        assert client.post("/auth/login", json=body).status_code == 422

    def test_password_is_never_echoed_back(self, client):
        assert PASSWORD not in _login(client).text
        assert PASSWORD not in _login(client, password="wrong-password-x").text

    def test_an_app_with_no_credentials_configured_lets_nobody_log_in(self):
        client = TestClient(create_app())

        assert _login(client).status_code == 401


class TestMe:

    def test_returns_the_token_owner_without_sensitive_fields(self, client):
        token = _login(client, "user-001").json()["access_token"]

        response = client.get("/auth/me", headers=_bearer(token))

        assert response.status_code == 200
        seed = next(u for u in SEED_USERS if u.id == "user-001")
        assert response.json() == {
            "user_id": seed.id, "name": seed.name, "department": seed.department,
            "role": seed.role, "clearance_level": seed.clearance_level, "api_role": "user",
        }

    def test_token_identifies_the_user_not_the_request(self, client):
        token = _login(client, "user-007").json()["access_token"]

        assert client.get("/auth/me", headers=_bearer(token)).json()["user_id"] == "user-007"

    def test_no_header_is_401(self, client):
        response = client.get("/auth/me")

        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"

    @pytest.mark.parametrize("header", [
        "Bearer", "Bearer ", "Token abc", "Basic dXNlcjpwYXNz", "bearer.garbage", "Bearer not.a.jwt",
    ])
    def test_malformed_authorization_headers_are_401(self, client, header):
        assert client.get("/auth/me", headers={"Authorization": header}).status_code == 401

    def test_tampered_token_is_401(self, client):
        token = _login(client).json()["access_token"]
        header, payload, signature = token.split(".")
        forged = ".".join([header, payload, ("A" if signature[0] != "A" else "B") + signature[1:]])

        assert client.get("/auth/me", headers=_bearer(forged)).status_code == 401

    def test_token_signed_with_another_secret_is_401(self, client, clock):
        issued = int(clock.now().timestamp())
        forged = jwt.encode({"sub": "user-007", "iss": "governai", "iat": issued, "exp": issued + 3600},
                            "x" * 40, algorithm="HS256")

        assert client.get("/auth/me", headers=_bearer(forged)).status_code == 401

    def test_expired_token_is_401(self, client, clock):
        token = _login(client).json()["access_token"]
        clock.advance(3600)

        assert client.get("/auth/me", headers=_bearer(token)).status_code == 401

    def test_token_of_a_user_deleted_since_is_401(self, client, users):
        token = _login(client, "user-001").json()["access_token"]
        users.remove(next(u for u in users if u.id == "user-001"))

        assert client.get("/auth/me", headers=_bearer(token)).status_code == 401

    def test_all_401_bodies_are_identical(self, client, clock):
        good = _login(client).json()["access_token"]
        clock.advance(3600)
        bodies = {
            client.get("/auth/me").text,
            client.get("/auth/me", headers=_bearer("garbage")).text,
            client.get("/auth/me", headers=_bearer(good)).text,
        }

        assert len(bodies) == 1
