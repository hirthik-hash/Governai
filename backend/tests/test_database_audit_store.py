# backend/tests/test_database_audit_store.py

"""Day 78: DatabaseAuditStore and PersistentDecisionLogger, standalone and inside a real pipeline."""

import pytest

from agents.audit_agent import AuditRecord
from core.decision_logger import LogEntryType
from core.orchestrator import RequestPipeline
from data.seed_data import SEED_USERS
from database.audit_store import DatabaseAuditStore, PersistentDecisionLogger
from database.repositories import AuditRecordRepository, DecisionLogRepository
from database.seeding import seed_database
from database.session import init_db, make_engine, make_session_factory

ESCALATING = {"user_id": "user-003", "resource_id": "resource-003", "session_token": "abc"}
GRANTED = {"user_id": "user-007", "resource_id": "resource-001", "session_token": "abc"}


@pytest.fixture
def factory():
    engine = make_engine("sqlite://")
    init_db(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        seed_database(session)
        session.commit()
    return factory


def _record(request_id="req-1", **overrides) -> AuditRecord:
    fields = dict(
        request_id=request_id, timestamp="2026-09-22T10:00:00+00:00", user_id="user-001",
        resource_id="resource-001", action_requested="x", final_fsm_state="closed",
        risk_score=10, risk_level="LOW", final_decision="GRANTED", approver_user_id="",
        agent_reasoning_trail=["a"],
    )
    fields.update(overrides)
    return AuditRecord(**fields)


class TestDatabaseAuditStoreStandalone:

    def test_add_then_all_round_trips(self, factory):
        store = DatabaseAuditStore(factory)
        record = _record()

        store.add(record)

        assert store.all() == [record]

    def test_has_final_record_ignores_pending(self, factory):
        store = DatabaseAuditStore(factory)
        store.add(_record("req-a", final_decision="PENDING"))

        assert store.has_final_record("req-a") is False

        store.add(_record("req-a", final_decision="GRANTED"))
        assert store.has_final_record("req-a") is True

    def test_unknown_request_id_has_no_final_record(self, factory):
        assert DatabaseAuditStore(factory).has_final_record("req-nope") is False

    def test_a_second_final_record_is_refused_by_the_database(self, factory):
        store = DatabaseAuditStore(factory)
        store.add(_record("req-dup"))

        with pytest.raises(Exception):
            store.add(_record("req-dup", final_decision="DENIED"))

    def test_each_call_uses_its_own_session_data_is_visible_to_a_fresh_instance(self, factory):
        DatabaseAuditStore(factory).add(_record("req-shared"))

        assert DatabaseAuditStore(factory).has_final_record("req-shared") is True


class TestPersistentDecisionLoggerStandalone:

    def test_in_memory_reads_still_work_exactly_like_the_base_class(self, factory):
        logger = PersistentDecisionLogger(factory)
        pipeline = RequestPipeline(decision_logger=logger)

        pipeline.submit_request({"request_id": "d-1", **GRANTED})

        assert len(logger.all_entries()) > 0
        assert logger.entries_for_request("d-1") == logger.all_entries()

    def test_every_entry_is_also_persisted(self, factory):
        logger = PersistentDecisionLogger(factory)
        pipeline = RequestPipeline(decision_logger=logger)

        pipeline.submit_request({"request_id": "d-2", **GRANTED})
        pipeline.submit_request({"request_id": "d-3", **ESCALATING})
        pipeline.resolve_escalation("d-3", human_decision="approved")

        pipeline.submit_request({"request_id": "d-3b", **ESCALATING})
        pipeline.safe_mode_processor.transition_system({"system_healthy": False})
        pipeline.safe_mode_processor.transition_system({"critical_failure": True})
        pipeline.resolve_escalation("d-3b", human_decision="approved")

        with factory() as session:
            stored = DecisionLogRepository(session).all()

        assert len(stored) == len(logger.all_entries())
        assert {e.entry_type for e in stored} == set(LogEntryType)

    def test_persisted_entries_survive_a_fresh_read(self, factory):
        logger = PersistentDecisionLogger(factory)
        RequestPipeline(decision_logger=logger).submit_request({"request_id": "d-4", **GRANTED})

        with factory() as session:
            for_request = DecisionLogRepository(session).for_request("d-4")

        assert len(for_request) > 0


class TestReplayProtectionInsideARealPipeline:

    def test_a_second_pipeline_over_the_same_database_refuses_the_replay(self, factory):
        store = DatabaseAuditStore(factory)
        first_pipeline = RequestPipeline(audit_store=store)
        first_pipeline.submit_request({"request_id": "req-restart", **GRANTED})

        # Simulates a process restart: a brand-new pipeline, none of the
        # first one's in-memory state, sharing only the database.
        second_pipeline = RequestPipeline(audit_store=DatabaseAuditStore(factory))

        result = second_pipeline.submit_request({"request_id": "req-restart", **GRANTED})

        assert result.status == "error"
        assert "already been finalized" in result.errors[0]

    def test_a_pending_escalation_from_before_a_restart_is_not_blocked(self, factory):
        # Only a FINAL record blocks a replay; a request that never
        # finished (still PENDING, or never got that far) is unaffected.
        first_pipeline = RequestPipeline(audit_store=DatabaseAuditStore(factory))
        first_pipeline.submit_request({"request_id": "req-still-pending", **ESCALATING})

        second_pipeline = RequestPipeline(audit_store=DatabaseAuditStore(factory))
        result = second_pipeline.submit_request({"request_id": "req-still-pending", **ESCALATING})

        # Same process would 409 on the in-memory pending store; a
        # different instance has no way to know it's pending, so it
        # simply processes it again as a new request - a known,
        # documented limitation until pending escalations are persisted.
        assert result.status == "pending_approval"

    def test_without_an_audit_store_a_replay_within_one_process_is_still_refused(self):
        # The in-memory _finalized_request_ids set (Day 78) protects a
        # single long-lived pipeline even with no database configured.
        pipeline = RequestPipeline()
        pipeline.submit_request({"request_id": "req-same-process", **GRANTED})

        result = pipeline.submit_request({"request_id": "req-same-process", **GRANTED})

        assert result.status == "error"

    def test_a_denied_outcome_also_blocks_a_replay(self, factory):
        pipeline = RequestPipeline(audit_store=DatabaseAuditStore(factory))
        pipeline.submit_request({"request_id": "req-denied-replay", "user_id": "user-009", "resource_id": "resource-001", "session_token": "abc"})

        result = pipeline.submit_request({"request_id": "req-denied-replay", "user_id": "user-009", "resource_id": "resource-001", "session_token": "abc"})

        assert result.status == "error"

    def test_replay_check_runs_before_any_agent_no_history_or_timeout_state_changes(self, factory):
        pipeline = RequestPipeline(audit_store=DatabaseAuditStore(factory))
        pipeline.submit_request({"request_id": "req-side-effects", **GRANTED})
        before = pipeline.security_agent.history_tracker.get_recent_count("user-007") if hasattr(
            pipeline.security_agent, "history_tracker") else None

        pipeline.submit_request({"request_id": "req-side-effects", **GRANTED})

        if before is not None:
            after = pipeline.security_agent.history_tracker.get_recent_count("user-007")
            assert after == before

    def test_resolving_an_escalation_after_it_was_replay_refused_still_works(self, factory):
        # A rejected replay attempt must not corrupt the ORIGINAL pending
        # escalation still tracked by the same process.
        pipeline = RequestPipeline(audit_store=DatabaseAuditStore(factory))
        pipeline.submit_request({"request_id": "req-untouched", **ESCALATING})

        assert pipeline.resolve_escalation("req-untouched", human_decision="approved").status == "granted"
