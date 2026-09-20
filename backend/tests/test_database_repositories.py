# backend/tests/test_database_repositories.py

"""
Day 74: CRUD repositories over the persistence models.

Every test runs against a private in-memory SQLite database seeded with
the standard seed data.
"""

import pytest
from sqlalchemy.exc import IntegrityError

from agents.audit_agent import AuditRecord
from agents.request_agent import find_resources_by_name
from core.decision_logger import DecisionLogger, LogEntryType
from core.orchestrator import RequestPipeline
from data.seed_data import SEED_RESOURCES, SEED_USERS, Resource, Sensitivity, User
from database.repositories import (
    AuditRecordRepository, DecisionLogRepository, DuplicateRecordError, InvalidReportingChainError,
    RecordInUseError, RecordNotFoundError, ResourceRepository, UserRepository,
)
from database.seeding import seed_database
from database.session import init_db, make_engine, make_session_factory


@pytest.fixture
def session():
    engine = make_engine("sqlite://")
    init_db(engine)
    s = make_session_factory(engine)()
    seed_database(s)
    s.commit()
    yield s
    s.rollback()
    s.close()


@pytest.fixture
def users(session):
    return UserRepository(session)


@pytest.fixture
def resources(session):
    return ResourceRepository(session)


def _user(user_id="user-100", **overrides) -> User:
    fields = dict(id=user_id, name="New Person", department="legal", role="Counsel", clearance_level=2, reports_to="user-007")
    fields.update(overrides)
    return User(**fields)


def _resource(resource_id="resource-100", **overrides) -> Resource:
    fields = dict(id=resource_id, name="Legal Hold Register", department="legal",
                  sensitivity=Sensitivity.RESTRICTED, resource_type="database")
    fields.update(overrides)
    return Resource(**fields)


def _record(request_id="req-1", **overrides) -> AuditRecord:
    fields = dict(
        request_id=request_id, timestamp="2026-09-21T10:00:00+00:00", user_id="user-001",
        resource_id="resource-001", action_requested="access request for resource-001",
        final_fsm_state="closed", risk_score=10, risk_level="LOW", final_decision="GRANTED",
        approver_user_id="", agent_reasoning_trail=["a"],
    )
    fields.update(overrides)
    return AuditRecord(**fields)


class TestUserReads:

    def test_get_returns_the_seed_user_exactly(self, users):
        assert users.get("user-004") == next(u for u in SEED_USERS if u.id == "user-004")

    def test_get_missing_raises_not_found_which_is_a_value_error(self, users):
        with pytest.raises(RecordNotFoundError):
            users.get("user-999")
        with pytest.raises(ValueError):  # what the agents' `except ValueError` relies on
            users.get("user-999")

    def test_list_all_is_ordered_and_matches_seed(self, users):
        assert users.list_all() == sorted(SEED_USERS, key=lambda u: u.id)

    def test_list_filters_by_department(self, users):
        assert {u.id for u in users.list_all(department="finance")} == {"user-003", "user-004", "user-010"}

    def test_exists(self, users):
        assert users.exists("user-001") and not users.exists("user-999")


class TestUserWrites:

    def test_add_then_get_round_trips(self, users):
        users.add(_user())

        assert users.get("user-100") == _user()

    def test_add_duplicate_id_is_rejected(self, users):
        with pytest.raises(DuplicateRecordError):
            users.add(_user("user-001"))

    def test_add_with_unknown_approver_is_rejected(self, users):
        with pytest.raises(RecordNotFoundError):
            users.add(_user(reports_to="user-999"))

    def test_add_reporting_to_self_is_rejected(self, users):
        with pytest.raises(InvalidReportingChainError):
            users.add(_user("user-100", reports_to="user-100"))

    def test_add_with_no_approver_is_allowed(self, users):
        users.add(_user(reports_to=""))

        assert users.get("user-100").reports_to == ""

    def test_update_changes_every_mutable_field(self, users):
        updated = User(id="user-001", name="Alex Chen-Park", department="security", role="Analyst",
                       clearance_level=3, is_blacklisted=True, reports_to="user-006")

        users.update(updated)

        assert users.get("user-001") == updated

    def test_blacklisting_a_user_via_update(self, users):
        original = users.get("user-002")

        users.update(User(**{**original.__dict__, "is_blacklisted": True}))

        assert users.get("user-002").is_blacklisted is True

    def test_update_missing_user_is_not_found(self, users):
        with pytest.raises(RecordNotFoundError):
            users.update(_user("user-999"))

    def test_update_clearance_out_of_range_is_rejected_by_the_database(self, users):
        original = users.get("user-001")

        with pytest.raises(IntegrityError):
            users.update(User(**{**original.__dict__, "clearance_level": 9}))

    def test_update_to_report_to_self_is_rejected(self, users):
        original = users.get("user-001")

        with pytest.raises(InvalidReportingChainError):
            users.update(User(**{**original.__dict__, "reports_to": "user-001"}))

    def test_update_that_closes_a_reporting_loop_is_rejected(self, users):
        # Seed chain: user-001 -> user-002 -> user-007. Making user-007 report to
        # user-001 would close the loop.
        ciso = users.get("user-007")

        with pytest.raises(InvalidReportingChainError):
            users.update(User(**{**ciso.__dict__, "reports_to": "user-001"}))

    def test_update_to_a_valid_new_approver_is_allowed(self, users):
        original = users.get("user-001")

        users.update(User(**{**original.__dict__, "reports_to": "user-006"}))

        assert users.get("user-001").reports_to == "user-006"

    def test_update_with_unknown_approver_is_rejected(self, users):
        original = users.get("user-001")

        with pytest.raises(RecordNotFoundError):
            users.update(User(**{**original.__dict__, "reports_to": "user-999"}))


class TestUserDelete:

    def test_delete_unreferenced_user(self, users):
        users.add(_user())

        users.delete("user-100")

        assert not users.exists("user-100")

    def test_delete_user_with_subordinates_is_refused(self, users):
        with pytest.raises(RecordInUseError, match="report to them"):
            users.delete("user-002")  # user-001 and user-008 report to user-002

    def test_delete_user_in_the_audit_ledger_is_refused(self, users, session):
        AuditRecordRepository(session).add(_record(user_id="user-005"))

        with pytest.raises(RecordInUseError, match="audit records"):
            users.delete("user-005")

    def test_delete_user_who_approved_a_record_is_refused(self, users, session):
        AuditRecordRepository(session).add(_record(approver_user_id="user-004"))

        with pytest.raises(RecordInUseError):
            users.delete("user-004")

    def test_delete_missing_user_is_not_found(self, users):
        with pytest.raises(RecordNotFoundError):
            users.delete("user-999")


class TestResourceRepository:

    def test_get_and_list_match_seed(self, resources):
        assert resources.get("resource-005") == next(r for r in SEED_RESOURCES if r.id == "resource-005")
        assert resources.list_all() == sorted(SEED_RESOURCES, key=lambda r: r.id)

    def test_get_missing_is_a_value_error(self, resources):
        with pytest.raises(ValueError):
            resources.get("resource-999")

    def test_list_filters(self, resources):
        assert {r.id for r in resources.list_all(sensitivity=Sensitivity.TOP_SECRET)} == {"resource-005", "resource-006"}
        assert {r.id for r in resources.list_all(department="hr")} == {"resource-001", "resource-005"}
        assert [r.id for r in resources.list_all(department="hr", sensitivity=Sensitivity.PUBLIC)] == ["resource-001"]

    def test_add_get_update_delete_lifecycle(self, resources):
        resources.add(_resource())
        assert resources.get("resource-100") == _resource()

        resources.update(_resource(name="Legal Hold Registry", sensitivity=Sensitivity.TOP_SECRET))
        assert resources.get("resource-100").required_clearance == 5

        resources.delete("resource-100")
        assert not resources.exists("resource-100")

    def test_add_duplicate_is_rejected(self, resources):
        with pytest.raises(DuplicateRecordError):
            resources.add(_resource("resource-001"))

    def test_update_and_delete_missing_are_not_found(self, resources):
        with pytest.raises(RecordNotFoundError):
            resources.update(_resource("resource-999"))
        with pytest.raises(RecordNotFoundError):
            resources.delete("resource-999")

    def test_delete_resource_in_the_audit_ledger_is_refused(self, resources, session):
        AuditRecordRepository(session).add(_record(resource_id="resource-003"))

        with pytest.raises(RecordInUseError):
            resources.delete("resource-003")

    @pytest.mark.parametrize("query", ["", "e", "E", "employee", "EMPLOYEE", "Q4", "documents", "zzz", "  ", "log", "on"])
    def test_find_by_name_matches_the_seed_lookup_exactly(self, resources, query):
        assert resources.find_by_name(query) == find_resources_by_name(query)

    def test_find_by_name_treats_percent_and_underscore_literally(self, resources):
        resources.add(_resource("resource-101", name="100% Coverage_Plan"))

        assert [r.id for r in resources.find_by_name("100%")] == ["resource-101"]
        assert [r.id for r in resources.find_by_name("_Plan")] == ["resource-101"]
        assert resources.find_by_name("%") == [resources.get("resource-101")]

    def test_find_by_name_handles_non_ascii_case_folding(self, resources):
        resources.add(_resource("resource-102", name="ÉCOLE Records"))

        assert [r.id for r in resources.find_by_name("école")] == ["resource-102"]


class TestAuditRecordRepository:

    def test_add_and_read_back_in_insertion_order(self, session):
        repo = AuditRecordRepository(session)
        first, second = _record("req-a"), _record("req-b", final_decision="DENIED")

        repo.add(first)
        repo.add(second)

        assert repo.all() == [first, second]

    def test_for_request_returns_only_that_requests_records(self, session):
        repo = AuditRecordRepository(session)
        repo.add(_record("req-a", final_decision="PENDING"))
        repo.add(_record("req-b"))
        repo.add(_record("req-a", final_decision="GRANTED"))

        assert [r.final_decision for r in repo.for_request("req-a")] == ["PENDING", "GRANTED"]

    def test_has_final_record_ignores_pending(self, session):
        repo = AuditRecordRepository(session)
        assert repo.has_final_record("req-a") is False

        repo.add(_record("req-a", final_decision="PENDING"))
        assert repo.has_final_record("req-a") is False

        repo.add(_record("req-a", final_decision="GRANTED"))
        assert repo.has_final_record("req-a") is True

    def test_has_final_record_counts_denied_too(self, session):
        repo = AuditRecordRepository(session)
        repo.add(_record("req-d", final_decision="DENIED"))

        assert repo.has_final_record("req-d") is True

    def test_a_second_final_record_is_still_refused_by_the_database(self, session):
        repo = AuditRecordRepository(session)
        repo.add(_record("req-a"))

        with pytest.raises(IntegrityError):
            repo.add(_record("req-a", final_decision="DENIED"))


class TestDecisionLogRepository:

    def _logged_pipeline_run(self) -> DecisionLogger:
        logger = DecisionLogger()
        pipeline = RequestPipeline(decision_logger=logger)
        pipeline.submit_request({"request_id": "d-1", "user_id": "user-007", "resource_id": "resource-001", "session_token": "abc"})
        pipeline.submit_request({"request_id": "d-2", "user_id": "user-003", "resource_id": "resource-003", "session_token": "abc"})
        pipeline.resolve_escalation("d-2", human_decision="approved")
        pipeline.safe_mode_processor.transition_system({"system_healthy": False})
        return logger

    def test_add_many_preserves_order_and_content(self, session):
        logger = self._logged_pipeline_run()
        repo = DecisionLogRepository(session)

        repo.add_many(logger.all_entries())

        stored = repo.all()
        assert [(e.entry_type, e.from_state, e.to_state, e.request_id) for e in stored] == [
            (e.entry_type, e.from_state, e.to_state, e.request_id) for e in logger.all_entries()
        ]

    def test_for_request_matches_the_in_memory_logger(self, session):
        logger = self._logged_pipeline_run()
        repo = DecisionLogRepository(session)
        repo.add_many(logger.all_entries())

        for request_id in ("d-1", "d-2"):
            assert [e.description for e in repo.for_request(request_id)] == [
                e.description for e in logger.entries_for_request(request_id)
            ]

    def test_by_type_matches_the_in_memory_logger(self, session):
        logger = self._logged_pipeline_run()
        repo = DecisionLogRepository(session)
        repo.add_many(logger.all_entries())

        for entry_type in (LogEntryType.REQUEST_TRANSITION, LogEntryType.SYSTEM_TRANSITION):
            assert len(repo.by_type(entry_type)) == len(logger.entries_by_type(entry_type))
        assert len(repo.by_type(LogEntryType.SYSTEM_TRANSITION)) == 1

    def test_add_single_entry(self, session):
        logger = self._logged_pipeline_run()
        repo = DecisionLogRepository(session)

        repo.add(logger.all_entries()[0])

        assert len(repo.all()) == 1
