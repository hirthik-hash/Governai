# backend/tests/test_policy_agent_integration.py

"""
Day 98: AI layer integration tests - the close-out day for Phase 4.

Unlike Days 89-97's per-method test files (one capability, isolated
fixtures), this file builds ONE shared policy library and ONE shared
PolicyIntelligenceAgent instance, then exercises Q&A, citation
grounding, conflict detection, and decision explanation IN SEQUENCE
against it - proving the methods compose correctly on shared state,
not just that each works in isolation.

The agent itself holds no mutable state between calls (only
self._ollama, self._session_factory, self._top_k - set once at
construction); every method opens its own short-lived session per call,
the same pattern as every other Database* class since Day 75. These
tests verify that architectural claim empirically rather than just
asserting it in a docstring.

A second class, TestFullRealWorkflow, runs the exact same sequence
against a genuine local Ollama server, skipped automatically if one is
not reachable - the actual "AI layer integration test" this project's
own roadmap names for Day 98.
"""

import pytest

from agents.policy_agent import PolicyIntelligenceAgent
from ai.fake_ollama_client import FakeOllamaClient
from ai.policy_ingestion import ingest_document
from core.config import settings
from core.orchestrator import RequestPipeline
from database.repositories import AuditRecordRepository, PolicyRepository
from database.seeding import seed_database
from database.session import init_db, make_engine, make_session_factory
from tests.policy_fixtures import make_docx, make_pdf

UPLOADED_AT = "2026-10-02T10:00:00+00:00"


@pytest.fixture
def session_factory():
    engine = make_engine("sqlite://")
    init_db(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        seed_database(session)  # users/resources - audit_records needs real FK targets
        session.commit()
    return factory


class TestSharedLibraryAcrossCapabilities:
    """One agent instance, one policy library, every capability exercised in sequence."""

    def _seed_library(self, session_factory):
        with session_factory() as session:
            repo = PolicyRepository(session)
            repo.create_document("Escalation Policy", "escalation.pdf", UPLOADED_AT, [
                "Escalation timeout is thirty minutes for restricted resources.",
                "Approved escalations grant access immediately.",
            ])
            repo.create_document("Conflicting Escalation Policy", "conflicting.pdf", UPLOADED_AT, [
                "Escalation timeout is ninety minutes for restricted resources.",
            ])
            repo.create_document("HR Policy", "hr.pdf", UPLOADED_AT, [
                "Vacation requests are approved by HR within five business days.",
            ])
            session.commit()

    def test_qa_then_conflict_detection_then_explanation_all_use_the_same_agent(self, session_factory):
        self._seed_library(session_factory)
        ollama = FakeOllamaClient(default_response="placeholder")
        agent = PolicyIntelligenceAgent(ollama, session_factory)

        # 1. Q&A against the shared library.
        ollama.default_response = "The timeout is thirty minutes, per [Excerpt 1]."
        qa_result = agent.process({"question": "What is the escalation timeout?"})
        assert qa_result.success is True
        assert qa_result.data["grounded"] is True
        assert qa_result.data["grounding_warning"] is False

        # 2. Conflict detection against the SAME library - the two
        # escalation documents genuinely disagree (30 vs 90 minutes).
        ollama.default_response = "CONFLICT: the two documents specify different timeout durations."
        conflict_result = agent.detect_conflicts(min_shared_terms=3)
        assert conflict_result.success is True
        assert len(conflict_result.data["conflicts"]) >= 1

        # 3. A real governance decision, explained by the same agent.
        pipeline = RequestPipeline()
        decision = pipeline.submit_request({
            "request_id": "req-integration-1", "user_id": "user-007",
            "resource_id": "resource-001", "session_token": "abc",
        })
        assert decision.status == "granted"
        with session_factory() as session:
            AuditRecordRepository(session).add(decision.audit_record)
            session.commit()

        ollama.default_response = "The request was granted because the risk was low."
        explanation = agent.explain_decision("req-integration-1")
        assert explanation.success is True
        assert explanation.data["outcome_consistent"] is True

        # 4. Prove nothing leaked across calls: a fresh Q&A on an
        # unrelated topic still only sees ITS matching chunk, and the
        # conflict/explanation calls above left no trace in the agent.
        ollama.default_response = "Approved within five business days, per [Excerpt 1]."
        hr_result = agent.process({"question": "How many days for vacation approval?"})
        assert hr_result.success is True
        assert len(hr_result.data["excerpts"]) == 1
        assert "five business days" in hr_result.data["excerpts"][0]["text"]

    def test_ingested_pdf_and_docx_coexist_in_one_library_for_every_capability(self, session_factory, tmp_path):
        make_pdf(tmp_path / "pdf_policy.pdf", ["Server maintenance occurs every Sunday at 3am UTC."])
        make_docx(tmp_path / "docx_policy.docx", ["Server maintenance occurs every Sunday at 4am UTC for EU servers."])
        with session_factory() as session:
            ingest_document(session, "PDF Maintenance Policy", str(tmp_path / "pdf_policy.pdf"))
            ingest_document(session, "DOCX Maintenance Policy", str(tmp_path / "docx_policy.docx"))
            session.commit()

        ollama = FakeOllamaClient(default_response="placeholder")
        agent = PolicyIntelligenceAgent(ollama, session_factory)

        ollama.default_response = "Maintenance is Sunday, per [Excerpt 1] and [Excerpt 2]."
        qa_result = agent.process({"question": "When does server maintenance occur?"})
        assert qa_result.success is True
        assert len(qa_result.data["excerpts"]) == 2  # one from each ingested format

        ollama.default_response = "CONFLICT: the two documents specify different maintenance times."
        conflict_result = agent.detect_conflicts(min_shared_terms=3)
        assert conflict_result.data["chunks_considered"] == 2
        assert len(conflict_result.data["conflicts"]) == 1

    def test_an_empty_library_behaves_consistently_across_every_capability(self, session_factory):
        ollama = FakeOllamaClient(default_response="should never be called")
        agent = PolicyIntelligenceAgent(ollama, session_factory)

        qa_result = agent.process({"question": "Anything at all?"})
        conflict_result = agent.detect_conflicts()

        assert qa_result.data["chunks_considered"] == 0
        assert conflict_result.data["chunks_considered"] == 0
        assert ollama.calls == []  # neither capability should call Ollama with nothing to work from


class TestAgentHoldsNoStateBetweenCalls:

    def test_two_sequential_calls_on_separate_agent_instances_sharing_one_db_agree(self, session_factory):
        with session_factory() as session:
            PolicyRepository(session).create_document("Policy", "p.pdf", UPLOADED_AT, ["Escalation timeout is thirty minutes."])
            session.commit()

        first_agent = PolicyIntelligenceAgent(FakeOllamaClient(default_response="30 minutes [Excerpt 1]."), session_factory)
        second_agent = PolicyIntelligenceAgent(FakeOllamaClient(default_response="30 minutes [Excerpt 1]."), session_factory)

        first_result = first_agent.process({"question": "escalation timeout"})
        second_result = second_agent.process({"question": "escalation timeout"})

        assert first_result.data["excerpts"] == second_result.data["excerpts"]

    def test_calling_detect_conflicts_repeatedly_is_idempotent(self, session_factory):
        with session_factory() as session:
            repo = PolicyRepository(session)
            repo.create_document("A", "a.pdf", UPLOADED_AT, ["Escalation timeout is thirty minutes."])
            repo.create_document("B", "b.pdf", UPLOADED_AT, ["Escalation timeout is ninety minutes."])
            session.commit()
        agent = PolicyIntelligenceAgent(FakeOllamaClient(default_response="CONFLICT: differs."), session_factory)

        first = agent.detect_conflicts(min_shared_terms=3)
        second = agent.detect_conflicts(min_shared_terms=3)

        assert first.data["candidates_checked"] == second.data["candidates_checked"]
        assert len(first.data["conflicts"]) == len(second.data["conflicts"])


class TestFullRealWorkflow:
    """
    The genuine end-to-end proof: one real local Ollama server, one real
    policy library (a real PDF and a real DOCX), a real RequestPipeline
    decision, and all four PolicyIntelligenceAgent capabilities run in
    sequence against them. Skipped automatically if Ollama is unreachable.
    """

    @pytest.fixture
    def ollama_client(self):
        from ai.ollama_client import OllamaClient
        client = OllamaClient(settings.ollama_base_url, settings.ollama_model)
        if not client.is_available():
            pytest.skip(f"no reachable Ollama at {settings.ollama_base_url}")
        yield client
        client.close()

    def test_the_full_workflow_end_to_end_against_real_ollama(self, session_factory, ollama_client, tmp_path):
        make_pdf(tmp_path / "policy_a.pdf", ["Escalation timeout is exactly thirty minutes for restricted resources."])
        make_docx(tmp_path / "policy_b.docx", ["Escalation timeout is exactly ninety minutes for restricted resources."])
        with session_factory() as session:
            ingest_document(session, "Policy A", str(tmp_path / "policy_a.pdf"))
            ingest_document(session, "Policy B", str(tmp_path / "policy_b.docx"))
            session.commit()

        agent = PolicyIntelligenceAgent(ollama_client, session_factory)

        qa_result = agent.process({"question": "What is the escalation timeout?"})
        assert qa_result.success is True
        assert qa_result.data["grounded"] is True

        conflict_result = agent.detect_conflicts(min_shared_terms=3)
        assert conflict_result.success is True
        assert len(conflict_result.data["conflicts"]) >= 1

        pipeline = RequestPipeline()
        decision = pipeline.submit_request({
            "request_id": "req-full-workflow", "user_id": "user-007",
            "resource_id": "resource-001", "session_token": "abc",
        })
        with session_factory() as session:
            AuditRecordRepository(session).add(decision.audit_record)
            session.commit()

        explanation = agent.explain_decision("req-full-workflow")
        assert explanation.success is True
        assert explanation.data["outcome_consistent"] is True
