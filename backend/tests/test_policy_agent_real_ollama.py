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

        assert result.data["grounded"] is True
        assert "five" in result.data["answer"].lower() or "5" in result.data["answer"]
