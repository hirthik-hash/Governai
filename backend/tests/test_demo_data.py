# backend/tests/test_demo_data.py

"""Day 75: the expanded demo dataset is internally consistent and works end to end."""

import pytest

from core.orchestrator import RequestPipeline
from data.demo_data import DEMO_RESOURCES, DEMO_USERS
from data.directory import SeedDirectory
from data.seed_data import SEED_RESOURCES, SEED_USERS, Sensitivity
from database.directory import DatabaseDirectory
from database.models import ResourceModel, UserModel
from database.seeding import seed_database
from database.session import init_db, make_engine, make_session_factory

MAX_CHAIN_HOPS = 10  # EscalationAgent.MAX_CHAIN_DEPTH


class TestDatasetShape:

    def test_extends_the_frozen_seed_data_exactly(self):
        assert DEMO_USERS[: len(SEED_USERS)] == SEED_USERS
        assert DEMO_RESOURCES[: len(SEED_RESOURCES)] == SEED_RESOURCES

    def test_sizes(self):
        assert (len(DEMO_USERS), len(DEMO_RESOURCES)) == (24, 20)

    def test_ids_are_unique(self):
        assert len({u.id for u in DEMO_USERS}) == len(DEMO_USERS)
        assert len({r.id for r in DEMO_RESOURCES}) == len(DEMO_RESOURCES)

    def test_clearance_levels_are_in_range(self):
        assert all(0 <= u.clearance_level <= 5 for u in DEMO_USERS)

    def test_covers_every_sensitivity_and_several_departments(self):
        assert {r.sensitivity for r in DEMO_RESOURCES} == set(Sensitivity)
        assert {"legal", "operations"} <= {u.department for u in DEMO_USERS}
        assert {"legal", "operations"} <= {r.department for r in DEMO_RESOURCES}

    def test_two_users_are_blacklisted(self):
        assert sorted(u.id for u in DEMO_USERS if u.is_blacklisted) == ["user-009", "user-024"]


class TestApprovalChains:

    def test_there_is_exactly_one_root_the_ciso(self):
        assert [u.id for u in DEMO_USERS if not u.reports_to] == ["user-007"]

    def test_every_chain_reaches_the_ciso_without_a_loop(self):
        by_id = {u.id: u for u in DEMO_USERS}

        for user in DEMO_USERS:
            current, hops = user, 0
            while current.reports_to:
                assert current.reports_to in by_id, f"{current.id} reports to a missing user"
                current = by_id[current.reports_to]
                hops += 1
                assert hops <= MAX_CHAIN_HOPS, f"{user.id} has a chain that never ends"
            assert current.id == "user-007"


class TestDatasetSeedsAndRuns:

    @pytest.fixture
    def factory(self):
        engine = make_engine("sqlite://")
        init_db(engine)
        factory = make_session_factory(engine)
        with factory() as session:
            seed_database(session, users=DEMO_USERS, resources=DEMO_RESOURCES)
            session.commit()
        return factory

    def test_seeds_into_the_database_in_full(self, factory):
        with factory() as session:
            assert session.query(UserModel).count() == 24
            assert session.query(ResourceModel).count() == 20

    def test_under_cleared_manager_is_skipped_on_the_way_to_a_qualified_approver(self, factory):
        # user-015 -> user-016 (clearance 2, too low for a restricted resource)
        # -> user-017 (clearance 4).
        pipeline = RequestPipeline(directory=DatabaseDirectory(factory))

        result = pipeline.submit_request(
            {"request_id": "demo-1", "user_id": "user-015", "resource_id": "resource-010", "session_token": "abc"}
        )

        assert result.status == "pending_approval"
        assert result.notification.approver_user_id == "user-017"

    def test_new_blacklisted_user_is_denied(self, factory):
        pipeline = RequestPipeline(directory=DatabaseDirectory(factory))

        result = pipeline.submit_request(
            {"user_id": "user-024", "resource_id": "resource-013", "session_token": "abc"}
        )

        assert result.status == "denied"

    @pytest.mark.parametrize("name,expected", [
        ("report", ["resource-003", "resource-015"]),
        ("employee", ["resource-001", "resource-005", "resource-009"]),
        ("litigation", ["resource-012"]),
    ])
    def test_fuzzy_names_resolve_or_report_ambiguity(self, factory, name, expected):
        directory = DatabaseDirectory(factory)

        assert [r.id for r in directory.find_resources_by_name(name)] == expected

    def test_ambiguous_name_asks_for_clarification(self, factory):
        pipeline = RequestPipeline(directory=DatabaseDirectory(factory))

        result = pipeline.submit_request({"user_id": "user-020", "resource_name": "report", "session_token": "abc"})

        assert result.status == "clarification_needed"
        assert result.candidate_resource_ids == ["resource-003", "resource-015"]

    def test_seed_and_database_directories_agree_on_the_demo_data(self, factory):
        seed, database = SeedDirectory(DEMO_USERS, DEMO_RESOURCES), DatabaseDirectory(factory)

        assert all(seed.get_user(u.id) == database.get_user(u.id) for u in DEMO_USERS)
        assert all(seed.get_resource(r.id) == database.get_resource(r.id) for r in DEMO_RESOURCES)
