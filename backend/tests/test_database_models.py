# backend/tests/test_database_models.py

"""
Day 71: SQLAlchemy models, engine/session plumbing, and seeding.

Every test uses a private in-memory SQLite database - no file is
created, and nothing is shared between tests.
"""

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from data.seed_data import SEED_RESOURCES, SEED_USERS, Resource, Sensitivity, User
from database.models import ResourceModel, UserModel
from database.seeding import seed_database
from database.session import init_db, make_engine, make_session_factory, session_scope


@pytest.fixture
def factory():
    engine = make_engine("sqlite://")
    init_db(engine)
    return make_session_factory(engine)


@pytest.fixture
def session(factory):
    # A plain session that is always rolled back: several tests
    # deliberately trigger an IntegrityError, after which a commit
    # (what session_scope would attempt) is invalid.
    s = factory()
    yield s
    s.rollback()
    s.close()


def _user(user_id="u-1", **overrides) -> User:
    fields = dict(id=user_id, name="Test User", department="engineering", role="Engineer", clearance_level=1)
    fields.update(overrides)
    return User(**fields)


class TestSchema:

    def test_expected_tables_are_created(self, factory):
        with session_scope(factory) as s:
            assert set(inspect(s.get_bind()).get_table_names()) == {"users", "resources", "audit_records"}

    def test_in_memory_data_is_visible_across_sessions(self, factory):
        with session_scope(factory) as s:
            s.add(UserModel.from_domain(_user("u-shared")))

        with session_scope(factory) as s:
            assert s.get(UserModel, "u-shared") is not None


class TestUserModel:

    def test_round_trip_preserves_every_field(self, session):
        original = _user("u-2", clearance_level=4, is_blacklisted=True, reports_to="")
        session.add(UserModel.from_domain(original))
        session.flush()

        assert session.get(UserModel, "u-2").to_domain() == original

    def test_empty_reports_to_is_stored_as_null(self, session):
        session.add(UserModel.from_domain(_user("u-top", reports_to="")))
        session.flush()

        raw = session.execute(text("SELECT reports_to FROM users WHERE id = 'u-top'")).scalar()
        assert raw is None

    @pytest.mark.parametrize("level", [-1, 6])
    def test_clearance_outside_0_to_5_is_rejected_by_the_database(self, session, level):
        session.add(UserModel.from_domain(_user("u-bad", clearance_level=level)))

        with pytest.raises(IntegrityError):
            session.flush()

    @pytest.mark.parametrize("level", [0, 5])
    def test_clearance_boundaries_are_accepted(self, session, level):
        session.add(UserModel.from_domain(_user(f"u-{level}", clearance_level=level)))
        session.flush()

    def test_reports_to_must_reference_an_existing_user(self, session):
        session.add(UserModel.from_domain(_user("u-orphan", reports_to="user-999")))

        with pytest.raises(IntegrityError):
            session.flush()


class TestResourceModel:

    def test_round_trip_preserves_every_field_and_derived_clearance(self, session):
        original = Resource(
            id="r-1", name="Vault", department="security",
            sensitivity=Sensitivity.TOP_SECRET, resource_type="system",
        )
        session.add(ResourceModel.from_domain(original))
        session.flush()

        restored = session.get(ResourceModel, "r-1").to_domain()
        assert restored == original
        assert restored.required_clearance == original.required_clearance == 5

    def test_sensitivity_is_stored_as_its_value(self, session):
        session.add(ResourceModel.from_domain(
            Resource(id="r-2", name="Doc", department="hr",
                     sensitivity=Sensitivity.RESTRICTED, resource_type="document")
        ))
        session.flush()

        assert session.execute(text("SELECT sensitivity FROM resources WHERE id = 'r-2'")).scalar() == "restricted"

    def test_unknown_sensitivity_is_rejected_by_the_database(self, session):
        with pytest.raises(IntegrityError):
            session.execute(text(
                "INSERT INTO resources (id, name, department, sensitivity, resource_type) "
                "VALUES ('r-bad', 'X', 'hr', 'bogus', 'document')"
            ))


class TestSeeding:

    def test_seed_data_round_trips_exactly(self, session):
        result = seed_database(session)

        assert (result.users_inserted, result.resources_inserted) == (len(SEED_USERS), len(SEED_RESOURCES))
        users = [m.to_domain() for m in session.scalars(select(UserModel).order_by(UserModel.id))]
        resources = [m.to_domain() for m in session.scalars(select(ResourceModel).order_by(ResourceModel.id))]
        assert users == sorted(SEED_USERS, key=lambda u: u.id)
        assert resources == sorted(SEED_RESOURCES, key=lambda r: r.id)

    def test_blacklisted_seed_user_stays_blacklisted(self, session):
        seed_database(session)

        assert session.get(UserModel, "user-009").is_blacklisted is True

    def test_seeding_twice_inserts_nothing_the_second_time(self, session):
        seed_database(session)

        second = seed_database(session)

        assert (second.users_inserted, second.resources_inserted) == (0, 0)
        assert session.query(UserModel).count() == len(SEED_USERS)

    def test_reseeding_never_overwrites_an_edited_row(self, session):
        seed_database(session)
        session.get(UserModel, "user-001").clearance_level = 3
        session.flush()

        seed_database(session)

        assert session.get(UserModel, "user-001").clearance_level == 3

    def test_users_are_inserted_regardless_of_input_order(self, session):
        seed_database(session, users=list(reversed(SEED_USERS)), resources=[])

        assert session.query(UserModel).count() == len(SEED_USERS)

    def test_reports_to_cycle_is_rejected(self, session):
        users = [_user("u-a", reports_to="u-b"), _user("u-b", reports_to="u-a")]

        with pytest.raises(ValueError, match="cycle"):
            seed_database(session, users=users, resources=[])

    def test_unknown_approver_is_rejected(self, session):
        with pytest.raises(ValueError, match="unknown user"):
            seed_database(session, users=[_user("u-c", reports_to="ghost")], resources=[])


class TestSessionScope:

    def test_commits_on_success(self, factory):
        with session_scope(factory) as s:
            s.add(UserModel.from_domain(_user("u-commit")))

        with session_scope(factory) as s:
            assert s.get(UserModel, "u-commit") is not None

    def test_rolls_back_on_exception(self, factory):
        with pytest.raises(RuntimeError):
            with session_scope(factory) as s:
                s.add(UserModel.from_domain(_user("u-rollback")))
                s.flush()
                raise RuntimeError("boom")

        with session_scope(factory) as s:
            assert s.get(UserModel, "u-rollback") is None
