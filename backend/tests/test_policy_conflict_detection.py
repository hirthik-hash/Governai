# backend/tests/test_policy_conflict_detection.py

"""Days 94-95: PolicyIntelligenceAgent.detect_conflicts(), against a real in-memory database and the fake Ollama clients."""

import pytest

from agents.policy_agent import PolicyIntelligenceAgent, _parse_conflict_verdict
from ai.fake_ollama_client import FakeOllamaClient, FakeOllamaClientSequence
from database.repositories import PolicyRepository
from database.session import init_db, make_engine, make_session_factory

UPLOADED_AT = "2026-09-27T10:00:00+00:00"


@pytest.fixture
def session_factory():
    engine = make_engine("sqlite://")
    init_db(engine)
    return make_session_factory(engine)


def _seed_two_documents(session_factory, chunks_a: list[str], chunks_b: list[str]) -> None:
    with session_factory() as session:
        repo = PolicyRepository(session)
        repo.create_document("Doc A", "a.pdf", UPLOADED_AT, chunks_a)
        repo.create_document("Doc B", "b.pdf", UPLOADED_AT, chunks_b)
        session.commit()


class TestParseConflictVerdict:

    def test_a_plain_conflict_response(self):
        assert _parse_conflict_verdict("CONFLICT: these disagree on the timeout value.") is True

    def test_a_plain_no_conflict_response(self):
        assert _parse_conflict_verdict("NO_CONFLICT: these are about different topics.") is False

    def test_no_conflict_with_a_space_instead_of_underscore(self):
        assert _parse_conflict_verdict("NO CONFLICT, unrelated topics.") is False

    def test_is_case_insensitive(self):
        assert _parse_conflict_verdict("no_conflict, these are fine together.") is False
        assert _parse_conflict_verdict("conflict! contradicts the other excerpt.") is True

    def test_no_conflict_is_never_misread_as_conflict(self):
        # The critical case: "CONFLICT" is a substring of "NO_CONFLICT".
        assert _parse_conflict_verdict("NO_CONFLICT") is False

    def test_an_unparseable_response_is_none(self):
        assert _parse_conflict_verdict("I'm not sure how to answer that.") is None

    def test_an_empty_response_is_none(self):
        assert _parse_conflict_verdict("") is None


class TestNotEnoughChunksToCompare:

    def test_empty_library(self, session_factory):
        agent = PolicyIntelligenceAgent(FakeOllamaClient(), session_factory)

        result = agent.detect_conflicts()

        assert result.success is True
        assert result.data["conflicts"] == []
        assert result.data["candidates_checked"] == 0

    def test_a_single_chunk_cannot_be_compared_to_anything(self, session_factory):
        with session_factory() as session:
            PolicyRepository(session).create_document("Solo", "s.pdf", UPLOADED_AT, ["Only one chunk here."])
            session.commit()
        agent = PolicyIntelligenceAgent(FakeOllamaClient(), session_factory)

        result = agent.detect_conflicts()

        assert result.data["chunks_considered"] == 1
        assert result.data["candidates_checked"] == 0


class TestNoCrossDocumentCandidates:

    def test_only_within_document_overlap_finds_no_candidates(self, session_factory):
        with session_factory() as session:
            repo = PolicyRepository(session)
            repo.create_document("Doc", "d.pdf", UPLOADED_AT, [
                "Escalation timeout resources apply.",
                "Escalation timeout resources differ.",
            ])
            session.commit()
        ollama = FakeOllamaClient(default_response="should never be called")
        agent = PolicyIntelligenceAgent(ollama, session_factory)

        result = agent.detect_conflicts(min_shared_terms=3)

        assert result.data["candidates_checked"] == 0
        assert ollama.calls == []

    def test_unrelated_documents_find_no_candidates_and_never_call_ollama(self, session_factory):
        _seed_two_documents(
            session_factory,
            ["Vacation requests are approved by HR within five business days."],
            ["Server backups run nightly at 2am UTC."],
        )
        ollama = FakeOllamaClient(default_response="should never be called")
        agent = PolicyIntelligenceAgent(ollama, session_factory)

        result = agent.detect_conflicts()

        assert result.data["candidates_checked"] == 0
        assert ollama.calls == []


class TestGenuineConflictDetected:

    def test_a_real_conflict_is_reported_with_both_sides(self, session_factory):
        _seed_two_documents(
            session_factory,
            ["Escalation timeout is thirty minutes for restricted resources."],
            ["Escalation timeout is forty five minutes for restricted resources."],
        )
        ollama = FakeOllamaClient(default_response="CONFLICT: the two documents specify different timeout durations.")
        agent = PolicyIntelligenceAgent(ollama, session_factory)

        result = agent.detect_conflicts(min_shared_terms=3)

        assert result.data["candidates_checked"] == 1
        assert len(result.data["conflicts"]) == 1
        conflict = result.data["conflicts"][0]
        assert conflict["document_a"] == 1 and conflict["document_b"] == 2
        assert "thirty minutes" in conflict["text_a"]
        assert "forty five minutes" in conflict["text_b"]
        assert "different timeout durations" in conflict["ollama_explanation"]

    def test_shared_terms_are_included_in_the_report(self, session_factory):
        _seed_two_documents(
            session_factory,
            ["Escalation timeout resources apply here."],
            ["Escalation timeout resources differ here."],
        )
        ollama = FakeOllamaClient(default_response="CONFLICT: disagreement found.")
        agent = PolicyIntelligenceAgent(ollama, session_factory)

        result = agent.detect_conflicts(min_shared_terms=3)

        assert set(result.data["conflicts"][0]["shared_terms"]) >= {"escalation", "timeout", "resources"}

    def test_a_no_conflict_verdict_reports_zero_conflicts(self, session_factory):
        _seed_two_documents(
            session_factory,
            ["Escalation timeout resources apply here."],
            ["Escalation timeout resources differ here."],
        )
        ollama = FakeOllamaClient(default_response="NO_CONFLICT: both describe compatible scenarios.")
        agent = PolicyIntelligenceAgent(ollama, session_factory)

        result = agent.detect_conflicts(min_shared_terms=3)

        assert result.data["candidates_checked"] == 1
        assert result.data["conflicts"] == []


class TestUndeterminedResponses:

    def test_an_unparseable_response_is_counted_undetermined_not_a_conflict(self, session_factory):
        _seed_two_documents(
            session_factory,
            ["Escalation timeout resources apply here."],
            ["Escalation timeout resources differ here."],
        )
        ollama = FakeOllamaClient(default_response="These excerpts discuss escalation policies.")
        agent = PolicyIntelligenceAgent(ollama, session_factory)

        result = agent.detect_conflicts(min_shared_terms=3)

        assert result.data["conflicts"] == []
        assert result.data["undetermined"] == 1


class TestMultipleCandidatesAndCapping:

    def test_each_candidate_gets_its_own_ollama_call_in_order(self, session_factory):
        _seed_two_documents(
            session_factory,
            ["Escalation timeout resources apply here.", "Approval chain resources matter greatly."],
            ["Escalation timeout resources differ here.", "Approval chain resources vary widely."],
        )
        ollama = FakeOllamaClientSequence(["NO_CONFLICT: fine.", "CONFLICT: chain differs."])
        agent = PolicyIntelligenceAgent(ollama, session_factory)

        result = agent.detect_conflicts(min_shared_terms=3)

        assert result.data["candidates_checked"] == 2
        assert len(result.data["conflicts"]) == 1
        assert len(ollama.calls) == 2

    def test_max_candidates_caps_how_many_are_checked_and_reports_the_rest_as_skipped(self, session_factory):
        chunks_a = [f"Escalation timeout resources variant {i} apply here." for i in range(5)]
        chunks_b = [f"Escalation timeout resources variant {i} differ here." for i in range(5)]
        _seed_two_documents(session_factory, chunks_a, chunks_b)
        ollama = FakeOllamaClient(default_response="NO_CONFLICT: fine.")
        agent = PolicyIntelligenceAgent(ollama, session_factory)

        result = agent.detect_conflicts(min_shared_terms=3, max_candidates=3)

        assert result.data["candidates_checked"] == 3
        assert result.data["candidates_skipped"] > 0
        assert len(ollama.calls) == 3


class TestOllamaFailureDuringConflictCheck:

    def test_an_ollama_error_is_a_failure_not_a_crash(self, session_factory):
        from ai.ollama_client import OllamaUnavailableError
        _seed_two_documents(
            session_factory,
            ["Escalation timeout resources apply here."],
            ["Escalation timeout resources differ here."],
        )
        ollama = FakeOllamaClientSequence([OllamaUnavailableError("connection refused")])
        agent = PolicyIntelligenceAgent(ollama, session_factory)

        result = agent.detect_conflicts(min_shared_terms=3)

        assert result.success is False
        assert "connection refused" in result.errors[0]
