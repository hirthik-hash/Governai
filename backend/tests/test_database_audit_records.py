# backend/tests/test_database_audit_records.py

"""
Day 72: the audit_records table - schema constraints, the
one-final-decision-per-request rule, the append-only guard, and an
integration check that every AuditRecord a real RequestPipeline
produces can be stored and read back unchanged.
"""

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from agents.audit_agent import AuditRecord
from core.orchestrator import RequestPipeline
from data.seed_data import SEED_USERS
from database.models import AuditRecordImmutableError, AuditRecordModel, UserModel
from database.seeding import seed_database
from database.session import init_db, make_engine, make_session_factory
from fsm.states import SystemState


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


def _record(request_id="req-1", **overrides) -> AuditRecord:
    fields = dict(
        request_id=request_id,
        timestamp="2026-09-21T10:00:00+00:00",
        user_id="user-001",
        resource_id="resource-001",
        action_requested="access request for resource-001",
        final_fsm_state="closed",
        risk_score=10,
        risk_level="LOW",
        final_decision="GRANTED",
        approver_user_id="",
        agent_reasoning_trail=["first", "second"],
    )
    fields.update(overrides)
    return AuditRecord(**fields)


def _store(session, record):
    model = AuditRecordModel.from_domain(record)
    session.add(model)
    session.flush()
    return model


class TestRoundTrip:

    def test_every_field_survives(self, session):
        original = _record(approver_user_id="user-002", policy_rule_cited="POL-7")

        model = _store(session, original)

        assert model.to_domain() == original

    def test_empty_approver_is_stored_as_null_and_restored(self, session):
        model = _store(session, _record(approver_user_id=""))

        assert model.approver_user_id is None
        assert model.to_domain().approver_user_id == ""

    def test_reasoning_trail_keeps_order_and_special_characters(self, session):
        trail = ["z first", "a | pipe, comma \"quote\"", "ünïcode ✓", "last"]

        model = _store(session, _record(agent_reasoning_trail=trail))
        session.expire_all()

        assert session.get(AuditRecordModel, model.id).to_domain().agent_reasoning_trail == trail

    def test_table_is_created(self, session):
        assert "audit_records" in inspect(session.get_bind()).get_table_names()


class TestColumnConstraints:

    @pytest.mark.parametrize("field,value", [
        ("user_id", "user-999"),
        ("resource_id", "resource-999"),
        ("approver_user_id", "user-999"),
    ])
    def test_foreign_keys_must_reference_existing_rows(self, session, field, value):
        session.add(AuditRecordModel.from_domain(_record(**{field: value})))

        with pytest.raises(IntegrityError):
            session.flush()

    def test_unknown_final_decision_is_rejected(self, session):
        session.add(AuditRecordModel.from_domain(_record(final_decision="MAYBE")))

        with pytest.raises(IntegrityError):
            session.flush()

    @pytest.mark.parametrize("score", [-1, 101])
    def test_risk_score_outside_0_to_100_is_rejected(self, session, score):
        session.add(AuditRecordModel.from_domain(_record(risk_score=score)))

        with pytest.raises(IntegrityError):
            session.flush()

    @pytest.mark.parametrize("score", [0, 100])
    def test_risk_score_boundaries_are_accepted(self, session, score):
        _store(session, _record(f"req-{score}", risk_score=score))

    def test_a_user_in_the_ledger_cannot_be_deleted(self, session):
        _store(session, _record())
        session.commit()

        session.delete(session.get(UserModel, "user-001"))

        with pytest.raises(IntegrityError):
            session.flush()


class TestOneFinalDecisionPerRequest:

    def test_two_granted_records_for_one_request_are_rejected(self, session):
        _store(session, _record("req-dup", final_decision="GRANTED"))
        session.add(AuditRecordModel.from_domain(_record("req-dup", final_decision="GRANTED")))

        with pytest.raises(IntegrityError):
            session.flush()

    def test_a_request_cannot_be_both_granted_and_denied(self, session):
        _store(session, _record("req-both", final_decision="GRANTED"))
        session.add(AuditRecordModel.from_domain(_record("req-both", final_decision="DENIED")))

        with pytest.raises(IntegrityError):
            session.flush()

    def test_repeated_pending_records_are_allowed(self, session):
        _store(session, _record("req-blocked", final_decision="PENDING"))
        _store(session, _record("req-blocked", final_decision="PENDING"))

        assert session.query(AuditRecordModel).filter_by(request_id="req-blocked").count() == 2

    def test_pending_records_then_one_final_record_is_allowed(self, session):
        _store(session, _record("req-flow", final_decision="PENDING"))
        _store(session, _record("req-flow", final_decision="PENDING"))
        _store(session, _record("req-flow", final_decision="GRANTED"))

        session.add(AuditRecordModel.from_domain(_record("req-flow", final_decision="DENIED")))
        with pytest.raises(IntegrityError):
            session.flush()

    def test_different_requests_do_not_conflict(self, session):
        _store(session, _record("req-a"))
        _store(session, _record("req-b"))


class TestAppendOnly:

    def test_updating_a_stored_record_is_refused(self, session):
        model = _store(session, _record())

        model.risk_score = 99

        with pytest.raises(AuditRecordImmutableError):
            session.flush()

    def test_deleting_a_stored_record_is_refused(self, session):
        model = _store(session, _record())

        session.delete(model)

        with pytest.raises(AuditRecordImmutableError):
            session.flush()

    def test_the_record_is_unchanged_after_a_refused_update(self, session):
        model = _store(session, _record(risk_score=10))
        session.commit()
        model.risk_score = 99
        with pytest.raises(AuditRecordImmutableError):
            session.flush()
        session.rollback()

        assert session.get(AuditRecordModel, model.id).risk_score == 10


ESCALATING = {"user_id": "user-003", "resource_id": "resource-003", "session_token": "abc"}


class TestRealPipelineRecordsFit:
    """
    Every record a real pipeline emits - across every outcome - must
    store and read back equal. If the schema were too strict for what
    the agents actually produce, this is where it would show.
    """

    def _run_scenarios(self) -> RequestPipeline:
        pipeline = RequestPipeline()
        pipeline.submit_request({"request_id": "s-granted", "user_id": "user-007", "resource_id": "resource-001", "session_token": "abc"})
        pipeline.submit_request({"request_id": "s-denied", "user_id": "user-009", "resource_id": "resource-001", "session_token": "abc"})
        pipeline.submit_request({**ESCALATING, "request_id": "s-approved"})
        pipeline.resolve_escalation("s-approved", human_decision="approved")
        pipeline.submit_request({**ESCALATING, "request_id": "s-rejected"})
        pipeline.resolve_escalation("s-rejected", human_decision="rejected")

        pipeline.submit_request({**ESCALATING, "request_id": "s-blocked"})
        pipeline.recovery_fsm.state = SystemState.SAFE_MODE_ACTIVE
        pipeline.resolve_escalation("s-blocked", human_decision="approved")
        pipeline.resolve_escalation("s-blocked", human_decision="approved")
        pipeline.recovery_fsm.state = SystemState.SYSTEM_NORMAL
        pipeline.resolve_escalation("s-blocked", human_decision="approved")
        return pipeline

    def test_every_pipeline_record_round_trips(self, session):
        pipeline = self._run_scenarios()
        assert len(pipeline.audit_records) == 7

        for record in pipeline.audit_records:
            session.add(AuditRecordModel.from_domain(record))
        session.flush()
        session.expire_all()

        stored = [m.to_domain() for m in session.scalars(select(AuditRecordModel).order_by(AuditRecordModel.id))]
        assert stored == pipeline.audit_records

    def test_blocked_request_has_two_pending_records_and_one_final(self, session):
        pipeline = self._run_scenarios()
        for record in pipeline.audit_records:
            session.add(AuditRecordModel.from_domain(record))
        session.flush()

        decisions = [
            m.final_decision
            for m in session.scalars(select(AuditRecordModel).where(AuditRecordModel.request_id == "s-blocked").order_by(AuditRecordModel.id))
        ]
        assert decisions == ["PENDING", "PENDING", "GRANTED"]

    def test_replaying_a_completed_request_id_is_caught_by_the_database(self, session):
        pipeline = RequestPipeline()
        first = pipeline.submit_request({"request_id": "s-replay", "user_id": "user-007", "resource_id": "resource-001", "session_token": "abc"})
        second = pipeline.submit_request({"request_id": "s-replay", "user_id": "user-007", "resource_id": "resource-001", "session_token": "abc"})
        session.add(AuditRecordModel.from_domain(first.audit_record))
        session.flush()

        session.add(AuditRecordModel.from_domain(second.audit_record))

        with pytest.raises(IntegrityError):
            session.flush()
