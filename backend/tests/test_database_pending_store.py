# backend/tests/test_database_pending_store.py

"""
Day 79: DatabasePendingEscalationStore, and RequestPipeline restoring
and persisting pending escalations across a real process boundary.
"""

from datetime import datetime, timedelta, timezone

import pytest

from agents.base_agent import AgentResult
from agents.escalation_agent import NotificationRecord
from core.orchestrator import RequestPipeline
from core.timeout_tracker import EscalationTimeoutTracker
from database.pending_store import DatabasePendingEscalationStore
from database.session import init_db, make_engine, make_session_factory
from fsm.states import RequestState, SystemState

ESCALATING = {"user_id": "user-003", "resource_id": "resource-003", "session_token": "abc"}
GRANTED = {"user_id": "user-007", "resource_id": "resource-001", "session_token": "abc"}


class FakeClock:
    def __init__(self):
        self.current = datetime(2026, 9, 22, 10, 0, tzinfo=timezone.utc)

    def now(self):
        return self.current

    def advance(self, seconds):
        self.current += timedelta(seconds=seconds)


@pytest.fixture
def factory():
    engine = make_engine("sqlite://")
    init_db(engine)
    return make_session_factory(engine)


@pytest.fixture
def clock():
    return FakeClock()


def _pipeline(factory, clock, store=None):
    return RequestPipeline(
        pending_store=store if store is not None else DatabasePendingEscalationStore(factory),
        timeout_tracker=EscalationTimeoutTracker(now_fn=clock.now),
    )


class TestStoreStandalone:

    def test_save_then_load_all_round_trips(self, factory):
        store = DatabasePendingEscalationStore(factory)
        result = AgentResult(success=True, data={"x": 1}, reasoning="did a thing", errors=[])

        store.save("req-1", "manager_review", {"a": 1, "notification": {"request_id": "req-1"}}, [result],
                  datetime(2026, 1, 1, tzinfo=timezone.utc), 1800)

        loaded = store.load_all()
        assert len(loaded) == 1
        row = loaded[0]
        assert row.request_id == "req-1"
        assert row.fsm_state == "manager_review"
        assert row.context == {"a": 1, "notification": {"request_id": "req-1"}}
        assert row.agent_results == [result]
        assert row.timeout_sent_at == datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert row.timeout_seconds == 1800

    def test_save_is_an_upsert(self, factory):
        store = DatabasePendingEscalationStore(factory)
        store.save("req-1", "manager_review", {"v": 1}, [], datetime.now(timezone.utc))

        store.save("req-1", "manager_review", {"v": 2}, [], datetime.now(timezone.utc))

        loaded = store.load_all()
        assert len(loaded) == 1
        assert loaded[0].context == {"v": 2}

    def test_delete_removes_the_row(self, factory):
        store = DatabasePendingEscalationStore(factory)
        store.save("req-1", "manager_review", {}, [], datetime.now(timezone.utc))

        store.delete("req-1")

        assert store.load_all() == []

    def test_deleting_an_unknown_id_does_not_raise(self, factory):
        DatabasePendingEscalationStore(factory).delete("req-nope")

    def test_load_all_is_ordered_by_insertion(self, factory):
        store = DatabasePendingEscalationStore(factory)
        for request_id in ("req-a", "req-b", "req-c"):
            store.save(request_id, "manager_review", {}, [], datetime.now(timezone.utc))

        assert [r.request_id for r in store.load_all()] == ["req-a", "req-b", "req-c"]

    def test_none_timeout_seconds_round_trips_as_none(self, factory):
        store = DatabasePendingEscalationStore(factory)
        store.save("req-1", "manager_review", {}, [], datetime.now(timezone.utc), timeout_seconds=None)

        assert store.load_all()[0].timeout_seconds is None

    def test_failed_agent_result_round_trips_with_its_errors(self, factory):
        store = DatabasePendingEscalationStore(factory)
        result = AgentResult(success=False, data={}, reasoning="", errors=["something broke"])
        store.save("req-1", "manager_review", {}, [result], datetime.now(timezone.utc))

        assert store.load_all()[0].agent_results == [result]


class TestPipelineParksAndUnparksThroughTheStore:

    def test_a_new_escalation_is_saved(self, factory, clock):
        store = DatabasePendingEscalationStore(factory)
        _pipeline(factory, clock, store).submit_request({**ESCALATING, "request_id": "req-park-1"})

        loaded = store.load_all()
        assert len(loaded) == 1 and loaded[0].request_id == "req-park-1"

    def test_a_granted_resolution_deletes_the_row(self, factory, clock):
        store = DatabasePendingEscalationStore(factory)
        pipeline = _pipeline(factory, clock, store)
        pipeline.submit_request({**ESCALATING, "request_id": "req-park-2"})

        pipeline.resolve_escalation("req-park-2", human_decision="approved")

        assert store.load_all() == []

    def test_a_rejected_resolution_also_deletes_the_row(self, factory, clock):
        store = DatabasePendingEscalationStore(factory)
        pipeline = _pipeline(factory, clock, store)
        pipeline.submit_request({**ESCALATING, "request_id": "req-park-3"})

        pipeline.resolve_escalation("req-park-3", human_decision="rejected")

        assert store.load_all() == []

    def test_a_safe_mode_block_re_saves_rather_than_deletes(self, factory, clock):
        store = DatabasePendingEscalationStore(factory)
        pipeline = _pipeline(factory, clock, store)
        pipeline.submit_request({**ESCALATING, "request_id": "req-park-4"})
        pipeline.recovery_fsm.state = SystemState.SAFE_MODE_ACTIVE

        pipeline.resolve_escalation("req-park-4", human_decision="approved")

        assert store.load_all()[0].request_id == "req-park-4"

    def test_an_undecided_check_re_saves_rather_than_deletes(self, factory, clock):
        store = DatabasePendingEscalationStore(factory)
        pipeline = _pipeline(factory, clock, store)
        pipeline.submit_request({**ESCALATING, "request_id": "req-park-5"})

        pipeline.resolve_escalation("req-park-5")  # no decision, not timed out yet

        assert store.load_all()[0].request_id == "req-park-5"

    def test_a_granted_direct_request_is_never_saved(self, factory, clock):
        store = DatabasePendingEscalationStore(factory)
        _pipeline(factory, clock, store).submit_request({**GRANTED, "request_id": "req-park-6"})

        assert store.load_all() == []

    def test_no_store_configured_behaves_exactly_as_before(self, clock):
        pipeline = RequestPipeline(timeout_tracker=EscalationTimeoutTracker(now_fn=clock.now))

        result = pipeline.submit_request({**ESCALATING, "request_id": "req-no-store"})

        assert result.status == "pending_approval"


class TestRestoreAcrossARealProcessBoundary:

    def test_a_fresh_pipeline_sees_a_pending_escalation_from_another_instance(self, factory, clock):
        first = _pipeline(factory, clock)
        first.submit_request({**ESCALATING, "request_id": "req-restore-1"})

        second = _pipeline(factory, clock)

        assert second.has_pending_request("req-restore-1")
        assert [n.request_id for n in second.list_pending_notifications()] == ["req-restore-1"]

    def test_the_notification_is_a_real_notificationrecord_not_a_dict(self, factory, clock):
        first = _pipeline(factory, clock)
        submitted = first.submit_request({**ESCALATING, "request_id": "req-restore-2"})

        restored = _pipeline(factory, clock)

        notification = restored.list_pending_notifications()[0]
        assert isinstance(notification, NotificationRecord)
        assert notification == submitted.notification

    def test_a_restored_escalation_can_be_approved(self, factory, clock):
        first = _pipeline(factory, clock)
        first.submit_request({**ESCALATING, "request_id": "req-restore-3"})

        restored = _pipeline(factory, clock)
        result = restored.resolve_escalation("req-restore-3", human_decision="approved")

        assert result.status == "granted"
        assert result.audit_record.final_decision == "GRANTED"

    def test_the_reasoning_trail_survives_the_restart_intact(self, factory, clock):
        first = _pipeline(factory, clock)
        first.submit_request({**ESCALATING, "request_id": "req-restore-4"})

        restored = _pipeline(factory, clock)
        result = restored.resolve_escalation("req-restore-4", human_decision="approved")

        trail = result.audit_record.agent_reasoning_trail
        assert any("routed to" in line for line in trail)
        assert any("approved by human decision" in line for line in trail)

    def test_the_fsm_resumes_from_manager_review_not_idle(self, factory, clock):
        first = _pipeline(factory, clock)
        first.submit_request({**ESCALATING, "request_id": "req-restore-5"})

        restored = _pipeline(factory, clock)

        fsm, _combined, _results = restored._pending_requests["req-restore-5"]
        assert fsm.state == RequestState.MANAGER_REVIEW

    def test_the_timeout_window_is_computed_from_the_original_send_time(self, factory):
        clock = FakeClock()
        first = _pipeline(factory, clock)
        first.submit_request({**ESCALATING, "request_id": "req-restore-6"})
        clock.advance(1900)  # past the 1800s default timeout, entirely before the "restart"

        restored = _pipeline(factory, clock)
        result = restored.resolve_escalation("req-restore-6")  # no decision -> checks timeout

        assert result.status == "denied"
        assert result.fsm_state == "denied_final"

    def test_a_nearly_timed_out_escalation_is_not_falsely_reset_by_the_restart(self, factory):
        clock = FakeClock()
        first = _pipeline(factory, clock)
        first.submit_request({**ESCALATING, "request_id": "req-restore-7"})
        clock.advance(1750)  # close to, but not past, the 1800s timeout

        restored = _pipeline(factory, clock)
        result = restored.resolve_escalation("req-restore-7", human_decision="approved")

        # A late human decision still overrides the (not-yet-expired) timeout.
        assert result.status == "granted"

    def test_multiple_pending_escalations_restore_in_original_order(self, factory, clock):
        first = _pipeline(factory, clock)
        first.submit_request({**ESCALATING, "request_id": "req-restore-8"})
        first.submit_request({"user_id": "user-001", "resource_id": "resource-003", "session_token": "abc", "request_id": "req-restore-9"})

        restored = _pipeline(factory, clock)

        assert [n.request_id for n in restored.list_pending_notifications()] == ["req-restore-8", "req-restore-9"]

    def test_restoring_does_not_touch_the_audit_store_or_finalized_ids(self, factory, clock):
        first = _pipeline(factory, clock)
        first.submit_request({**ESCALATING, "request_id": "req-restore-10"})

        restored = _pipeline(factory, clock)

        assert restored.audit_records == []
        assert restored._finalized_request_ids == set()

    def test_with_nothing_pending_restore_is_a_no_op(self, factory, clock):
        pipeline = _pipeline(factory, clock)

        assert pipeline._pending_requests == {}
