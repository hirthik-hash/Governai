# backend/tests/test_auth_roles.py

"""Day 77: API roles, their stores, and Principal resolution."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from auth.credentials import InMemoryCredentialStore
from auth.errors import AuthenticationError
from auth.passwords import PasswordService
from auth.roles import ROLE_ADMIN, ROLE_USER, VALID_ROLES, InMemoryRoleStore
from auth.seeding import seed_demo_roles
from auth.service import AuthService
from auth.tokens import TokenService
from data.demo_data import DEMO_ADMIN_USER_IDS, DEMO_USERS
from data.directory import SeedDirectory
from data.seed_data import SEED_USERS
from database.repositories import RecordNotFoundError, UserRepository
from database.roles import DatabaseRoleStore
from database.seeding import seed_database
from database.session import init_db, make_engine, make_session_factory
from tests.api_harness import FakeClock, PASSWORD, SECRET


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
    return InMemoryRoleStore() if request.param == "memory" else DatabaseRoleStore(database_factory)


def _auth(store, users=None):
    clock = FakeClock()
    service = AuthService(
        credentials=InMemoryCredentialStore(),
        directory=SeedDirectory(users=users),
        passwords=PasswordService(time_cost=1, memory_cost=8, parallelism=1),
        tokens=TokenService(SECRET, now_fn=clock.now),
        roles=store,
    )
    service.set_password("user-001", PASSWORD)
    return service


class TestRoleStores:

    def test_there_are_exactly_two_roles(self):
        assert VALID_ROLES == (ROLE_USER, ROLE_ADMIN)

    def test_everyone_is_a_plain_user_by_default(self, store):
        assert store.get_role("user-001") == ROLE_USER

    def test_unknown_users_read_as_a_plain_user_too(self, store):
        assert store.get_role("user-999") == ROLE_USER

    def test_set_and_change_a_role(self, store):
        store.set_role("user-001", ROLE_ADMIN)
        assert store.get_role("user-001") == ROLE_ADMIN

        store.set_role("user-001", ROLE_USER)
        assert store.get_role("user-001") == ROLE_USER

    @pytest.mark.parametrize("bad", ["superuser", "ADMIN", "", None])
    def test_invalid_roles_are_refused(self, store, bad):
        with pytest.raises(ValueError):
            store.set_role("user-001", bad)


class TestDatabaseRoleStore:

    def test_unknown_user_cannot_be_given_a_role(self, database_factory):
        with pytest.raises(RecordNotFoundError):
            DatabaseRoleStore(database_factory).set_role("user-999", ROLE_ADMIN)

    def test_the_database_itself_refuses_an_invalid_role(self, database_factory):
        with database_factory() as session:
            with pytest.raises(IntegrityError):
                session.execute(text("UPDATE users SET api_role = 'root' WHERE id = 'user-001'"))

    def test_role_never_reaches_the_domain_user_and_survives_user_updates(self, database_factory):
        store = DatabaseRoleStore(database_factory)
        store.set_role("user-001", ROLE_ADMIN)

        with database_factory() as session:
            repo = UserRepository(session)
            original = repo.get("user-001")
            assert original == next(u for u in SEED_USERS if u.id == "user-001")
            assert not hasattr(original, "api_role")
            repo.update(type(original)(**{**original.__dict__, "role": "Lead Engineer"}))
            session.commit()

        assert store.get_role("user-001") == ROLE_ADMIN

    def test_job_title_and_api_role_are_independent(self, database_factory):
        # user-007's job title is "CISO" but that gives no API role by itself.
        assert DatabaseRoleStore(database_factory).get_role("user-007") == ROLE_USER


class TestAuthServiceRoles:

    def test_principal_carries_the_current_role_and_the_raw_token(self, store):
        service = _auth(store)
        token = service.login("user-001", PASSWORD).access_token

        principal = service.authenticate_principal(token)

        assert (principal.user.id, principal.role, principal.token) == ("user-001", ROLE_USER, token)
        assert principal.is_admin is False

    def test_a_role_change_applies_to_tokens_already_issued(self, store):
        service = _auth(store)
        token = service.login("user-001", PASSWORD).access_token

        service.set_role("user-001", ROLE_ADMIN)

        assert service.authenticate_principal(token).is_admin is True

    def test_set_role_refuses_unknown_users_and_invalid_roles(self, store):
        service = _auth(store)

        with pytest.raises(ValueError):
            service.set_role("user-999", ROLE_ADMIN)
        with pytest.raises(ValueError):
            service.set_role("user-001", "root")

    def test_the_role_is_not_in_the_token(self, store):
        import jwt
        service = _auth(store)
        service.set_role("user-001", ROLE_ADMIN)
        token = service.login("user-001", PASSWORD).access_token

        claims = jwt.decode(token, SECRET, algorithms=["HS256"], options={"verify_exp": False, "verify_iat": False})

        assert "role" not in claims and ROLE_ADMIN not in claims.values()

    def test_a_bad_token_is_still_an_authentication_error(self, store):
        with pytest.raises(AuthenticationError):
            _auth(store).authenticate_principal("garbage")


class TestDemoRoles:

    def test_only_the_ciso_is_a_demo_admin(self):
        assert DEMO_ADMIN_USER_IDS == ["user-007"]
        assert next(u for u in DEMO_USERS if u.id == "user-007").role == "CISO"

    def test_seeding_is_repeatable(self, store):
        service = _auth(store)

        seed_demo_roles(service, ["user-007"])
        seed_demo_roles(service, ["user-007"])

        assert store.get_role("user-007") == ROLE_ADMIN
        assert store.get_role("user-001") == ROLE_USER
