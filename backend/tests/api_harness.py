# backend/tests/api_harness.py

"""
Shared test harness for the authenticated API (Day 77).

Builds a complete app in memory: the seed users, each with a password, the
CISO (user-007) as the only admin, a JwtSessionValidator wired into the
pipeline, and a fake clock so token expiry never needs a real wait.
harness.as_user("user-004") returns a small client whose every request
carries that user's bearer token.
"""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from agents.recovery_agent import FailureRecoveryAgent, HealthCheckResult
from api.app import create_app
from auth.credentials import InMemoryCredentialStore
from auth.passwords import PasswordService
from auth.roles import ROLE_ADMIN, InMemoryRoleStore
from auth.service import AuthService
from auth.session_validator import JwtSessionValidator
from auth.tokens import TokenService
from core.orchestrator import RequestPipeline
from data.directory import SeedDirectory
from data.seed_data import SEED_USERS

SECRET = "s" * 40
PASSWORD = "correct-horse-battery"
ADMIN_ID = "user-007"
START = datetime(2026, 9, 22, 10, 0, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self):
        self.current = START

    def now(self):
        return self.current

    def advance(self, seconds):
        self.current += timedelta(seconds=seconds)


class UserSession:
    """A TestClient that sends one user's bearer token on every call."""

    def __init__(self, client: TestClient, token: str):
        self.token = token
        self._client = client

    @property
    def headers(self):
        return {"Authorization": f"Bearer {self.token}"}

    def get(self, url, **kwargs):
        return self._client.get(url, headers=self.headers, **kwargs)

    def post(self, url, **kwargs):
        return self._client.post(url, headers=self.headers, **kwargs)


def _failing_integrity() -> HealthCheckResult:
    return HealthCheckResult(check_name="fsm_integrity", healthy=False, detail="simulated failure")


class ApiHarness:

    def __init__(self, failing_health: bool = False):
        self.clock = FakeClock()
        self.users = list(SEED_USERS)
        directory = SeedDirectory(users=self.users)

        self.tokens = TokenService(SECRET, expiry_minutes=60, now_fn=self.clock.now)
        self.roles = InMemoryRoleStore()
        self.roles.set_role(ADMIN_ID, ROLE_ADMIN)
        self.auth = AuthService(
            credentials=InMemoryCredentialStore(),
            directory=directory,
            passwords=PasswordService(time_cost=1, memory_cost=8, parallelism=1),
            tokens=self.tokens,
            roles=self.roles,
        )
        for user in self.users:
            self.auth.set_password(user.id, PASSWORD)

        self.pipeline = RequestPipeline(directory=directory, session_validator=JwtSessionValidator(self.tokens))
        recovery_agent = (
            FailureRecoveryAgent(checks=[_failing_integrity], recovery_fsm=self.pipeline.recovery_fsm)
            if failing_health else None
        )
        self.app = create_app(pipeline=self.pipeline, recovery_agent=recovery_agent, auth_service=self.auth)
        self.client = TestClient(self.app)

    def as_user(self, user_id: str) -> UserSession:
        return UserSession(self.client, self.auth.login(user_id, PASSWORD).access_token)

    def force_safe_mode(self) -> None:
        """Drives the RecoveryFSM into safe mode through the real health endpoint (failing_health=True)."""
        admin = self.as_user(ADMIN_ID)
        admin.post("/system/health/evaluate")
        admin.post("/system/health/evaluate")
