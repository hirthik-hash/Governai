# backend/tests/test_auth_service.py

"""
Day 76: AuthService (login and token authentication) against both
credential stores, plus the database credential store itself.
"""

import logging
from datetime import datetime, timedelta, timezone

import pytest

from auth.credentials import InMemoryCredentialStore
from auth.errors import AuthenticationError, TokenExpiredError, WeakPasswordError
from auth.passwords import PasswordService
from auth.service import AuthService
from auth.tokens import TokenService
from data.directory import SeedDirectory
from data.seed_data import SEED_USERS
from database.credentials import DatabaseCredentialStore
from database.models import UserModel
from database.repositories import RecordNotFoundError, UserRepository
from database.seeding import seed_database
from database.session import init_db, make_engine, make_session_factory

SECRET = "s" * 40
PASSWORD = "correct-horse-battery"
FAST = dict(time_cost=1, memory_cost=8, parallelism=1)
NOW = datetime(2026, 9, 22, 10, 0, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self):
        self.current = NOW

    def now(self):
        return self.current

    def advance(self, seconds):
        self.current += timedelta(seconds=seconds)


class CountingPasswords(PasswordService):
    def __init__(self):
        super().__init__(**FAST)
        self.dummy_calls = 0

    def verify_against_dummy(self, password):
        self.dummy_calls += 1
        super().verify_against_dummy(password)


@pytest.fixture
def database_factory():
    engine = make_engine("sqlite://")
    init_db(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        seed_database(session)
        session.commit()
    return factory


@pytest.fixture(params=["memory", "database"])
def store(request, database_factory):
    if request.param == "memory":
        return InMemoryCredentialStore()
    return DatabaseCredentialStore(database_factory)


@pytest.fixture
def clock():
    return FakeClock()


def _service(store, clock, directory=None, passwords=None):
    return AuthService(
        credentials=store,
        directory=directory or SeedDirectory(),
        passwords=passwords or PasswordService(**FAST),
        tokens=TokenService(SECRET, now_fn=clock.now),
    )


@pytest.fixture
def auth(store, clock):
    service = _service(store, clock)
    service.set_password("user-001", PASSWORD)
    return service


class TestLogin:

    def test_correct_password_returns_a_token_for_that_user(self, auth):
        issued = auth.login("user-001", PASSWORD)

        assert auth.authenticate_token(issued.access_token).id == "user-001"

    def test_wrong_password_is_refused(self, auth):
        with pytest.raises(AuthenticationError):
            auth.login("user-001", PASSWORD + "x")

    def test_unknown_user_is_refused(self, auth):
        with pytest.raises(AuthenticationError):
            auth.login("user-999", PASSWORD)

    def test_user_with_no_password_is_refused(self, auth):
        with pytest.raises(AuthenticationError):
            auth.login("user-002", PASSWORD)

    @pytest.mark.parametrize("user_id", ["", None])
    def test_empty_user_id_is_refused(self, auth, user_id):
        with pytest.raises(AuthenticationError):
            auth.login(user_id, PASSWORD)

    def test_every_failure_looks_identical_to_the_caller(self, auth):
        messages = set()
        for user_id, password in (("user-001", "wrong-password-x"), ("user-999", PASSWORD), ("user-002", PASSWORD)):
            with pytest.raises(AuthenticationError) as error:
                auth.login(user_id, password)
            messages.add(str(error.value))

        assert messages == {"Invalid credentials"}

    def test_a_user_missing_from_the_directory_cannot_log_in_even_with_a_stored_password(self, store, clock):
        users = list(SEED_USERS)
        service = _service(store, clock, directory=SeedDirectory(users=users))
        service.set_password("user-001", PASSWORD)
        users.remove(next(u for u in users if u.id == "user-001"))

        with pytest.raises(AuthenticationError):
            service.login("user-001", PASSWORD)

    def test_a_blacklisted_user_can_still_authenticate(self, store, clock):
        # Deliberate: blacklisting is the FSM's job. Their requests are
        # hard-denied and AUDITED; locking them out at the door would hide them.
        service = _service(store, clock)
        service.set_password("user-009", PASSWORD)

        user = service.authenticate_token(service.login("user-009", PASSWORD).access_token)

        assert user.is_blacklisted is True


class TestTimingEqualization:

    def test_unknown_or_passwordless_users_still_cost_a_hash_verification(self, store, clock):
        passwords = CountingPasswords()
        service = _service(store, clock, passwords=passwords)
        service.set_password("user-001", PASSWORD)

        for user_id in ("user-999", "user-002"):
            with pytest.raises(AuthenticationError):
                service.login(user_id, PASSWORD)
        assert passwords.dummy_calls == 2

        with pytest.raises(AuthenticationError):
            service.login("user-001", "wrong-password-x")
        assert passwords.dummy_calls == 2  # a real user's failure used a real verification


class TestSetPassword:

    def test_weak_password_is_refused_and_nothing_is_stored(self, store, clock):
        service = _service(store, clock)

        with pytest.raises(WeakPasswordError):
            service.set_password("user-001", "short")

        assert store.get_password_hash("user-001") is None

    def test_unknown_user_is_refused(self, store, clock):
        with pytest.raises(ValueError):
            _service(store, clock).set_password("user-999", PASSWORD)

    def test_changing_a_password_invalidates_the_old_one(self, auth):
        auth.set_password("user-001", "a-brand-new-password")

        with pytest.raises(AuthenticationError):
            auth.login("user-001", PASSWORD)
        auth.login("user-001", "a-brand-new-password")

    def test_stored_value_is_a_hash_not_the_password(self, auth, store):
        stored = store.get_password_hash("user-001")

        assert stored.startswith("$argon2id$") and PASSWORD not in stored


class TestRehashOnLogin:

    def test_stronger_costs_upgrade_the_stored_hash_at_login(self, store, clock):
        weak = PasswordService(time_cost=1, memory_cost=8, parallelism=1)
        store.set_password_hash("user-001", weak.hash(PASSWORD))
        old_hash = store.get_password_hash("user-001")
        stronger = PasswordService(time_cost=2, memory_cost=16, parallelism=1)
        service = _service(store, clock, passwords=stronger)

        service.login("user-001", PASSWORD)

        new_hash = store.get_password_hash("user-001")
        assert new_hash != old_hash and stronger.verify(new_hash, PASSWORD)

    def test_a_legacy_password_below_todays_policy_still_logs_in_and_upgrades(self, store, clock):
        weak = PasswordService(time_cost=1, memory_cost=8, parallelism=1)
        store.set_password_hash("user-001", weak.hash("short", enforce_policy=False))
        service = _service(store, clock, passwords=PasswordService(time_cost=2, memory_cost=16, parallelism=1))

        service.login("user-001", "short")


class TestAuthenticateToken:

    def test_expired_token(self, auth, clock):
        token = auth.login("user-001", PASSWORD).access_token
        clock.advance(3600)

        with pytest.raises(TokenExpiredError):
            auth.authenticate_token(token)

    def test_token_of_a_user_removed_since_is_refused(self, store, clock):
        users = list(SEED_USERS)
        service = _service(store, clock, directory=SeedDirectory(users=users))
        service.set_password("user-001", PASSWORD)
        token = service.login("user-001", PASSWORD).access_token
        users.remove(next(u for u in users if u.id == "user-001"))

        with pytest.raises(AuthenticationError):
            service.authenticate_token(token)


class TestLogging:

    def test_failures_are_logged_without_the_password(self, auth):
        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        logger = logging.getLogger("governai.auth")
        handler = Capture(level=logging.WARNING)
        logger.addHandler(handler)
        try:
            with pytest.raises(AuthenticationError):
                auth.login("user-001", "very-secret-guess-123")
        finally:
            logger.removeHandler(handler)

        assert any("login failed" in m and "user-001" in m for m in records)
        assert not any("very-secret-guess-123" in m for m in records)


class TestDatabaseCredentialStore:

    def test_user_without_a_password_has_none(self, database_factory):
        assert DatabaseCredentialStore(database_factory).get_password_hash("user-001") is None

    def test_unknown_user_reads_as_none_and_cannot_be_written(self, database_factory):
        store = DatabaseCredentialStore(database_factory)

        assert store.get_password_hash("user-999") is None
        with pytest.raises(RecordNotFoundError):
            store.set_password_hash("user-999", "hash")

    def test_the_hash_never_reaches_the_domain_user_and_survives_user_updates(self, database_factory):
        store = DatabaseCredentialStore(database_factory)
        store.set_password_hash("user-001", "stored-hash")

        with database_factory() as session:
            repo = UserRepository(session)
            original = repo.get("user-001")
            assert original == next(u for u in SEED_USERS if u.id == "user-001")
            assert not hasattr(original, "password_hash")

            repo.update(type(original)(**{**original.__dict__, "role": "Lead Engineer"}))
            session.commit()

        assert store.get_password_hash("user-001") == "stored-hash"

    def test_column_is_null_for_seeded_users(self, database_factory):
        with database_factory() as session:
            assert session.query(UserModel).filter(UserModel.password_hash.is_not(None)).count() == 0
