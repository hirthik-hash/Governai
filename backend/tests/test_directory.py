# backend/tests/test_directory.py

"""
Day 75: the Directory abstraction, its two implementations, and the
agents/pipeline that now receive one.
"""

import ast
from pathlib import Path

import pytest

from agents.escalation_agent import EscalationAgent
from agents.request_agent import RequestUnderstandingAgent
from core.orchestrator import RequestPipeline
from data.directory import Directory, SeedDirectory
from data.seed_data import SEED_RESOURCES, SEED_USERS, Resource, Sensitivity, User
from database.directory import DatabaseDirectory
from database.repositories import RecordNotFoundError
from database.seeding import seed_database
from database.session import init_db, make_engine, make_session_factory


@pytest.fixture
def database_directory():
    engine = make_engine("sqlite://")
    init_db(engine)
    factory = make_session_factory(engine)
    session = factory()
    seed_database(session)
    session.commit()
    session.close()
    return DatabaseDirectory(factory)


def _custom_directory() -> SeedDirectory:
    boss = User(id="u-boss", name="Boss", department="ops", role="Head", clearance_level=4, reports_to="")
    worker = User(id="u-work", name="Worker", department="ops", role="Clerk", clearance_level=1, reports_to="u-boss")
    vault = Resource(id="r-vault", name="Vault Ledger", department="ops",
                     sensitivity=Sensitivity.RESTRICTED, resource_type="database")
    return SeedDirectory(users=[boss, worker], resources=[vault])


class TestSeedDirectory:

    def test_default_reads_the_seed_data(self):
        directory = SeedDirectory()

        assert directory.get_user("user-004") == next(u for u in SEED_USERS if u.id == "user-004")
        assert directory.get_resource("resource-005") == next(r for r in SEED_RESOURCES if r.id == "resource-005")

    @pytest.mark.parametrize("lookup", ["get_user", "get_resource"])
    def test_unknown_id_raises_value_error(self, lookup):
        with pytest.raises(ValueError):
            getattr(SeedDirectory(), lookup)("nope-999")

    def test_custom_lists_replace_the_seed_data(self):
        directory = _custom_directory()

        assert directory.get_user("u-work").name == "Worker"
        with pytest.raises(ValueError):
            directory.get_user("user-001")

    def test_name_search_is_case_insensitive_and_in_list_order(self):
        assert [r.id for r in SeedDirectory().find_resources_by_name("EMPLOYEE")] == ["resource-001", "resource-005"]

    def test_both_directories_satisfy_the_protocol(self, database_directory):
        assert isinstance(SeedDirectory(), Directory)
        assert isinstance(database_directory, Directory)


class TestDatabaseDirectory:

    def test_lookups_equal_the_seed_directory(self, database_directory):
        seed = SeedDirectory()

        for user in SEED_USERS:
            assert database_directory.get_user(user.id) == seed.get_user(user.id)
        for resource in SEED_RESOURCES:
            assert database_directory.get_resource(resource.id) == seed.get_resource(resource.id)

    def test_unknown_ids_raise_not_found_which_is_a_value_error(self, database_directory):
        with pytest.raises(RecordNotFoundError):
            database_directory.get_user("user-999")
        with pytest.raises(ValueError):
            database_directory.get_resource("resource-999")

    def test_name_search_matches_the_seed_directory(self, database_directory):
        for query in ("employee", "REPORT", "e", "", "zzz"):
            assert database_directory.find_resources_by_name(query) == SeedDirectory().find_resources_by_name(query)


class TestAgentsUseTheInjectedDirectory:

    def test_request_agent_resolves_from_the_custom_directory(self):
        agent = RequestUnderstandingAgent(directory=_custom_directory())

        result = agent.process({"user_id": "u-work", "resource_id": "r-vault"})

        assert result.success and result.data["resolved_resource_id"] == "r-vault"
        assert result.data["required_clearance"] == 3

    def test_request_agent_does_not_see_seed_data_when_given_a_custom_directory(self):
        agent = RequestUnderstandingAgent(directory=_custom_directory())

        assert agent.process({"user_id": "user-001", "resource_id": "r-vault"}).success is False

    def test_escalation_agent_walks_the_custom_chain(self):
        agent = EscalationAgent(directory=_custom_directory())

        result = agent.process({"user_id": "u-work", "required_clearance": 3, "request_id": "r-1"})

        assert result.success and result.data["notification"].approver_user_id == "u-boss"

    def test_pipeline_shares_one_directory_across_its_agents(self):
        pipeline = RequestPipeline(directory=_custom_directory())

        submitted = pipeline.submit_request(
            {"request_id": "d-1", "user_id": "u-work", "resource_id": "r-vault", "session_token": "abc"}
        )

        assert submitted.status == "pending_approval"
        assert submitted.notification.approver_user_id == "u-boss"
        assert pipeline.resolve_escalation("d-1", human_decision="approved").status == "granted"

    def test_defaults_are_unchanged(self):
        assert RequestPipeline().submit_request(
            {"user_id": "user-007", "resource_id": "resource-001", "session_token": "abc"}
        ).status == "granted"


class TestRequestAgentModuleHasOneClass:

    def test_request_understanding_agent_is_defined_exactly_once(self):
        # Regression guard: the file once defined this class twice, and the
        # second silently shadowed the first.
        source = Path(__file__).resolve().parents[1].joinpath("agents", "request_agent.py").read_text(encoding="utf-8")

        classes = [n.name for n in ast.parse(source).body if isinstance(n, ast.ClassDef)]

        assert classes.count("RequestUnderstandingAgent") == 1
