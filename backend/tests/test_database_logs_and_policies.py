# backend/tests/test_database_logs_and_policies.py

"""
Day 73: the decision_log_entries table (persistent DecisionLogger) and
the policy_documents / policy_chunks tables.
"""

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from core.decision_logger import DecisionLogger, LogEntry, LogEntryType
from core.orchestrator import RequestPipeline
from database.models import (
    DecisionLogEntryModel, DecisionLogImmutableError, PolicyChunkModel, PolicyDocumentModel,
)
from database.serialization import to_json_safe
from database.session import init_db, make_engine, make_session_factory
from fsm.states import SystemState


@pytest.fixture
def session():
    engine = make_engine("sqlite://")
    init_db(engine)
    s = make_session_factory(engine)()
    yield s
    s.rollback()
    s.close()


def _entry(entry_type=LogEntryType.REQUEST_TRANSITION, **overrides) -> LogEntry:
    fields = dict(
        entry_type=entry_type,
        timestamp="2026-09-21T10:00:00+00:00",
        description="something happened",
        from_state="idle",
        to_state="request_received",
        request_id="req-1",
        context_snapshot={"clearance": 2},
    )
    fields.update(overrides)
    return LogEntry(**fields)


def _store(session, entry):
    model = DecisionLogEntryModel.from_domain(entry)
    session.add(model)
    session.flush()
    return model


SYSTEM_ENTRY = dict(entry_type=LogEntryType.SYSTEM_TRANSITION, request_id="",
                    from_state="system_normal", to_state="degraded_warning")
BLOCK_ENTRY = dict(entry_type=LogEntryType.SAFE_MODE_BLOCK, from_state="", to_state="")


class TestDecisionLogRoundTrip:

    @pytest.mark.parametrize("overrides", [{}, SYSTEM_ENTRY, BLOCK_ENTRY], ids=["request", "system", "block"])
    def test_every_entry_type_round_trips(self, session, overrides):
        original = _entry(**overrides)

        assert _store(session, original).to_domain() == original

    def test_empty_strings_are_stored_as_null(self, session):
        model = _store(session, _entry(**BLOCK_ENTRY))

        assert (model.from_state, model.to_state) == (None, None)

    def test_snapshot_with_a_dataclass_is_stored_as_json_safe_form(self, session):
        from agents.escalation_agent import NotificationRecord
        notification = NotificationRecord(
            request_id="req-1", approver_user_id="user-004", approver_name="Sofia Ricci",
            requester_user_id="user-003", requester_name="T", resource_summary="r",
            sent_at="2026-09-21T10:00:00+00:00", timeout_seconds=60,
        )
        original = _entry(context_snapshot={"notification": notification, "risk_score": 45})

        stored = _store(session, original).to_domain()

        assert stored.context_snapshot == to_json_safe(original.context_snapshot)
        assert stored.context_snapshot["notification"]["approver_user_id"] == "user-004"


class TestDecisionLogConstraints:

    def test_unknown_entry_type_is_rejected(self, session):
        with pytest.raises(IntegrityError):
            session.execute(text(
                "INSERT INTO decision_log_entries (entry_type, timestamp, description, context_snapshot) "
                "VALUES ('bogus', 't', 'd', '{}')"
            ))

    def test_system_transition_must_not_have_a_request_id(self, session):
        session.add(DecisionLogEntryModel.from_domain(_entry(**{**SYSTEM_ENTRY, "request_id": "req-1"})))

        with pytest.raises(IntegrityError):
            session.flush()

    @pytest.mark.parametrize("entry_type", [LogEntryType.REQUEST_TRANSITION, LogEntryType.SAFE_MODE_BLOCK])
    def test_request_scoped_entries_must_have_a_request_id(self, session, entry_type):
        states = {"from_state": "", "to_state": ""} if entry_type == LogEntryType.SAFE_MODE_BLOCK else {}
        session.add(DecisionLogEntryModel.from_domain(_entry(entry_type, request_id="", **states)))

        with pytest.raises(IntegrityError):
            session.flush()

    @pytest.mark.parametrize("missing", ["from_state", "to_state"])
    def test_a_transition_must_carry_both_states(self, session, missing):
        session.add(DecisionLogEntryModel.from_domain(_entry(**{missing: ""})))

        with pytest.raises(IntegrityError):
            session.flush()

    def test_a_safe_mode_block_must_not_carry_states(self, session):
        session.add(DecisionLogEntryModel.from_domain(_entry(LogEntryType.SAFE_MODE_BLOCK)))

        with pytest.raises(IntegrityError):
            session.flush()


class TestDecisionLogAppendOnly:

    def test_updating_an_entry_is_refused(self, session):
        model = _store(session, _entry())

        model.description = "rewritten"

        with pytest.raises(DecisionLogImmutableError):
            session.flush()

    def test_deleting_an_entry_is_refused(self, session):
        model = _store(session, _entry())

        session.delete(model)

        with pytest.raises(DecisionLogImmutableError):
            session.flush()


ESCALATING = {"user_id": "user-003", "resource_id": "resource-003", "session_token": "abc"}


class TestRealDecisionLogFits:
    """Every entry a real pipeline logs, of all three types, must store and read back."""

    def _run_scenarios(self) -> DecisionLogger:
        logger = DecisionLogger()
        pipeline = RequestPipeline(decision_logger=logger)
        pipeline.submit_request({"request_id": "l-granted", "user_id": "user-007", "resource_id": "resource-001", "session_token": "abc"})
        pipeline.submit_request({**ESCALATING, "request_id": "l-approved"})
        pipeline.resolve_escalation("l-approved", human_decision="approved")
        pipeline.submit_request({**ESCALATING, "request_id": "l-blocked"})
        pipeline.safe_mode_processor.transition_system({"system_healthy": False})
        pipeline.recovery_fsm.state = SystemState.SAFE_MODE_ACTIVE
        pipeline.resolve_escalation("l-blocked", human_decision="approved")
        return logger

    def test_all_three_entry_types_are_produced_and_stored(self, session):
        logger = self._run_scenarios()
        assert {e.entry_type for e in logger.all_entries()} == set(LogEntryType)

        for entry in logger.all_entries():
            session.add(DecisionLogEntryModel.from_domain(entry))
        session.flush()
        session.expire_all()

        stored = [m.to_domain() for m in session.scalars(select(DecisionLogEntryModel).order_by(DecisionLogEntryModel.id))]
        expected = [
            LogEntry(**{**e.__dict__, "context_snapshot": to_json_safe(e.context_snapshot)})
            for e in logger.all_entries()
        ]
        assert stored == expected

    def test_per_request_query_matches_the_in_memory_logger(self, session):
        logger = self._run_scenarios()
        for entry in logger.all_entries():
            session.add(DecisionLogEntryModel.from_domain(entry))
        session.flush()

        for request_id in ("l-granted", "l-approved", "l-blocked"):
            stored = session.scalars(
                select(DecisionLogEntryModel)
                .where(DecisionLogEntryModel.request_id == request_id)
                .order_by(DecisionLogEntryModel.id)
            ).all()
            assert [(m.from_state, m.to_state) for m in stored] == [
                (e.from_state or None, e.to_state or None) for e in logger.entries_for_request(request_id)
            ]


class TestPolicyTables:

    def _document(self, session, title="Access Policy") -> PolicyDocumentModel:
        doc = PolicyDocumentModel(title=title, source_filename="access.pdf", uploaded_at="2026-09-21T10:00:00+00:00")
        session.add(doc)
        session.flush()
        return doc

    def _chunk(self, session, document_id, index, text_="A paragraph."):
        chunk = PolicyChunkModel(document_id=document_id, chunk_index=index, text=text_)
        session.add(chunk)
        session.flush()
        return chunk

    def test_document_with_ordered_chunks_round_trips(self, session):
        doc = self._document(session)
        for i, paragraph in enumerate(["first", "second", "third"]):
            self._chunk(session, doc.id, i, paragraph)

        texts = session.scalars(
            select(PolicyChunkModel.text).where(PolicyChunkModel.document_id == doc.id).order_by(PolicyChunkModel.chunk_index)
        ).all()

        assert texts == ["first", "second", "third"]

    def test_a_chunk_index_cannot_repeat_within_a_document(self, session):
        doc = self._document(session)
        self._chunk(session, doc.id, 0)

        with pytest.raises(IntegrityError):
            self._chunk(session, doc.id, 0)

    def test_two_documents_may_each_have_a_chunk_zero(self, session):
        first, second = self._document(session, "A"), self._document(session, "B")

        self._chunk(session, first.id, 0)
        self._chunk(session, second.id, 0)

    def test_negative_chunk_index_is_rejected(self, session):
        doc = self._document(session)

        with pytest.raises(IntegrityError):
            self._chunk(session, doc.id, -1)

    def test_a_chunk_must_belong_to_an_existing_document(self, session):
        with pytest.raises(IntegrityError):
            self._chunk(session, 999, 0)

    def test_deleting_a_document_deletes_its_chunks_only(self, session):
        keep, drop = self._document(session, "Keep"), self._document(session, "Drop")
        for i in range(3):
            self._chunk(session, keep.id, i)
            self._chunk(session, drop.id, i)

        session.delete(drop)
        session.flush()

        remaining = session.scalars(select(func.count()).select_from(PolicyChunkModel)).one()
        assert remaining == 3
        assert session.scalars(select(PolicyChunkModel.document_id).distinct()).all() == [keep.id]
