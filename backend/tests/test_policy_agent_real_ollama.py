# backend/tests/test_policy_agent_real_ollama.py

"""
Days 89-91: the full real chain - a genuinely ingested PDF, a real
in-memory database, and a real local Ollama server - skipped
automatically if Ollama is not reachable (same pattern as Day 84's
test_ollama_client_real.py).
"""

import os

import pytest

from agents.policy_agent import PolicyIntelligenceAgent
from ai.ollama_client import OllamaClient
from ai.policy_ingestion import ingest_document
from core.config import settings
from database.session import init_db, make_engine, make_session_factory
from tests.policy_fixtures import make_pdf

TEST_MODEL = os.environ.get("OLLAMA_TEST_MODEL", settings.ollama_model)


@pytest.fixture
def session_factory():
    engine = make_engine("sqlite://")
    init_db(engine)
    return make_session_factory(engine)


@pytest.fixture
def ollama_client():
    client = OllamaClient(settings.ollama_base_url, TEST_MODEL)
    if not client.is_available():
        pytest.skip(f"no reachable Ollama at {settings.ollama_base_url}")
    yield client
    client.close()


class TestRealEndToEndPolicyQA:

    def test_answers_a_question_grounded_in_a_real_ingested_pdf(self, session_factory, ollama_client, tmp_path):
        make_pdf(tmp_path / "policy.pdf", [
            "Escalation Policy",
            "All escalations time out after exactly 45 minutes if no decision is made.",
            "Approved escalations grant access immediately.",
        ])
        with session_factory() as session:
            ingest_document(session, "Escalation Policy", str(tmp_path / "policy.pdf"))
            session.commit()

        agent = PolicyIntelligenceAgent(ollama_client, session_factory)
        result = agent.process({"question": "How many minutes until an escalation times out?"})

        assert result.success is True
        assert result.data["grounded"] is True
        assert "45" in result.data["answer"]

    def test_an_unrelated_question_against_the_same_library_still_grounds_correctly(self, session_factory, ollama_client, tmp_path):
        make_pdf(tmp_path / "policy.pdf", ["Vacation requests are approved by HR within five business days."])
        with session_factory() as session:
            ingest_document(session, "HR Policy", str(tmp_path / "policy.pdf"))
            session.commit()

        agent = PolicyIntelligenceAgent(ollama_client, session_factory)
        result = agent.process({"question": "How many business days for vacation approval?"})

        assert result.success is True
        assert result.data["grounded"] is True
        assert "five" in result.data["answer"].lower() or "5" in result.data["answer"]


class TestRealEndToEndConflictDetection:

    def test_a_real_numeric_conflict_between_two_documents_is_detected(self, session_factory, ollama_client, tmp_path):
        make_pdf(tmp_path / "doc_a.pdf", ["Escalation timeout is exactly thirty minutes for restricted resources."])
        make_pdf(tmp_path / "doc_b.pdf", ["Escalation timeout is exactly ninety minutes for restricted resources."])
        with session_factory() as session:
            ingest_document(session, "Doc A", str(tmp_path / "doc_a.pdf"))
            ingest_document(session, "Doc B", str(tmp_path / "doc_b.pdf"))
            session.commit()

        agent = PolicyIntelligenceAgent(ollama_client, session_factory)
        result = agent.detect_conflicts(min_shared_terms=3)

        assert result.success is True
        assert result.data["candidates_checked"] >= 1
        assert len(result.data["conflicts"]) >= 1

    def test_two_compatible_documents_on_different_topics_report_no_conflict(self, session_factory, ollama_client, tmp_path):
        make_pdf(tmp_path / "doc_a.pdf", ["Vacation requests are approved by HR within five business days."])
        make_pdf(tmp_path / "doc_b.pdf", ["Server backups run nightly at approximately 2am UTC."])
        with session_factory() as session:
            ingest_document(session, "Doc A", str(tmp_path / "doc_a.pdf"))
            ingest_document(session, "Doc B", str(tmp_path / "doc_b.pdf"))
            session.commit()

        agent = PolicyIntelligenceAgent(ollama_client, session_factory)
        result = agent.detect_conflicts(min_shared_terms=2)

        assert result.success is True
        assert result.data["conflicts"] == []


class TestRealEndToEndDecisionExplanation:
    """
    The most authentic proof for this feature: a REAL RequestPipeline
    (Days 1-81) produces a REAL AuditRecord, stored via the real
    AuditRecordRepository, then explained by a real local Ollama server -
    tying the deterministic governance core to the advisory AI layer
    exactly as this project's whole architecture intends.
    """

    def test_explains_a_real_granted_decision_from_the_real_pipeline(self, session_factory, ollama_client):
        from core.orchestrator import RequestPipeline
        from database.repositories import AuditRecordRepository
        from database.seeding import seed_database

        with session_factory() as seed_session:
            seed_database(seed_session)  # audit_records.user_id/resource_id are real foreign keys
            seed_session.commit()

        pipeline = RequestPipeline()
        result = pipeline.submit_request({
            "request_id": "req-explain-real-1", "user_id": "user-007",
            "resource_id": "resource-001", "session_token": "abc",
        })
        assert result.status == "granted"
        with session_factory() as session:
            AuditRecordRepository(session).add(result.audit_record)
            session.commit()

        agent = PolicyIntelligenceAgent(ollama_client, session_factory)
        explanation = agent.explain_decision("req-explain-real-1")

        assert explanation.success is True
        assert explanation.data["outcome_consistent"] is True
        assert len(explanation.data["narrative"]) > 0

    def test_explains_a_real_denied_decision_from_the_real_pipeline(self, session_factory, ollama_client):
        from core.orchestrator import RequestPipeline
        from database.repositories import AuditRecordRepository
        from database.seeding import seed_database

        with session_factory() as seed_session:
            seed_database(seed_session)  # audit_records.user_id/resource_id are real foreign keys
            seed_session.commit()

        pipeline = RequestPipeline()
        result = pipeline.submit_request({
            "request_id": "req-explain-real-2", "user_id": "user-009",  # blacklisted
            "resource_id": "resource-001", "session_token": "abc",
        })
        assert result.status == "denied"
        with session_factory() as session:
            AuditRecordRepository(session).add(result.audit_record)
            session.commit()

        agent = PolicyIntelligenceAgent(ollama_client, session_factory)
        explanation = agent.explain_decision("req-explain-real-2")

        assert explanation.success is True
        assert explanation.data["outcome_consistent"] is True
