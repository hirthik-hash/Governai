# backend/tests/test_policy_agent.py

"""Days 89-91: PolicyIntelligenceAgent's Q&A method, against a real in-memory database and the fake Ollama clients from Day 84."""

import pytest

from agents.policy_agent import PolicyIntelligenceAgent
from ai.fake_ollama_client import FakeOllamaClient, FakeOllamaClientSequence
from database.repositories import PolicyRepository
from database.session import init_db, make_engine, make_session_factory

UPLOADED_AT = "2026-09-26T10:00:00+00:00"


@pytest.fixture
def session_factory():
    engine = make_engine("sqlite://")
    init_db(engine)
    return make_session_factory(engine)


def _seed_policy(session_factory, chunks: list[str]) -> None:
    with session_factory() as session:
        PolicyRepository(session).create_document("Test Policy", "test.pdf", UPLOADED_AT, chunks)
        session.commit()


class TestNoPolicyLibrary:

    def test_empty_library_is_a_success_with_no_answer_attempted(self, session_factory):
        agent = PolicyIntelligenceAgent(FakeOllamaClient(default_response="should never be called"), session_factory)

        result = agent.process({"question": "What is the escalation timeout?"})

        assert result.success is True
        assert result.data["chunks_considered"] == 0
        assert result.data["grounded"] is False
        assert result.data["excerpts"] == []


class TestNoMatchingChunks:

    def test_a_question_matching_nothing_is_a_success_with_no_ollama_call(self, session_factory):
        _seed_policy(session_factory, ["Vacation requests are handled by HR."])
        ollama = FakeOllamaClient(default_response="should never be called")
        agent = PolicyIntelligenceAgent(ollama, session_factory)

        result = agent.process({"question": "What is the escalation timeout?"})

        assert result.success is True
        assert result.data["grounded"] is False
        assert result.data["chunks_considered"] == 1
        assert ollama.calls == []  # Ollama must not be called with nothing to ground it


class TestSuccessfulRetrievalAndAnswer:

    def test_retrieves_relevant_chunks_and_returns_ollamas_answer(self, session_factory):
        _seed_policy(session_factory, [
            "Escalation timeout is 30 minutes for restricted resources.",
            "Vacation requests are handled by HR.",
        ])
        ollama = FakeOllamaClient(default_response="The timeout is 30 minutes [Excerpt 1].")
        agent = PolicyIntelligenceAgent(ollama, session_factory)

        result = agent.process({"question": "What is the escalation timeout?"})

        assert result.success is True
        assert result.data["answer"] == "The timeout is 30 minutes [Excerpt 1]."
        assert result.data["grounded"] is True
        assert len(result.data["excerpts"]) == 1
        assert result.data["excerpts"][0]["label"] == "Excerpt 1"
        assert "Escalation timeout is 30 minutes" in result.data["excerpts"][0]["text"]

    def test_excerpts_are_labeled_sequentially_starting_at_one(self, session_factory):
        _seed_policy(session_factory, [
            "Escalation routes to a manager.",
            "Escalation timeout applies to restricted resources.",
            "Escalation may also apply to top secret resources.",
        ])
        agent = PolicyIntelligenceAgent(FakeOllamaClient(default_response="answer"), session_factory, top_k=3)

        result = agent.process({"question": "escalation timeout restricted top secret"})

        labels = [e["label"] for e in result.data["excerpts"]]
        assert labels == [f"Excerpt {i}" for i in range(1, len(labels) + 1)]

    def test_the_system_prompt_instructs_citation_and_grounding(self, session_factory):
        _seed_policy(session_factory, ["Escalation timeout is 30 minutes."])
        ollama = FakeOllamaClient(default_response="answer")
        agent = PolicyIntelligenceAgent(ollama, session_factory)

        agent.process({"question": "escalation timeout"})

        assert ollama.calls[0]["system"] is not None
        assert "cite" in ollama.calls[0]["system"].lower()

    def test_the_prompt_includes_the_labeled_excerpt_and_the_question(self, session_factory):
        _seed_policy(session_factory, ["Escalation timeout is 30 minutes."])
        ollama = FakeOllamaClient(default_response="answer")
        agent = PolicyIntelligenceAgent(ollama, session_factory)

        agent.process({"question": "What is the escalation timeout?"})

        prompt = ollama.calls[0]["prompt"]
        assert "[Excerpt 1] Escalation timeout is 30 minutes." in prompt
        assert "Question: What is the escalation timeout?" in prompt

    def test_top_k_limits_how_many_excerpts_are_retrieved(self, session_factory):
        _seed_policy(session_factory, [f"Escalation rule number {i}." for i in range(10)])
        agent = PolicyIntelligenceAgent(FakeOllamaClient(default_response="answer"), session_factory, top_k=2)

        result = agent.process({"question": "escalation"})

        assert len(result.data["excerpts"]) == 2

    def test_reasoning_reports_how_many_chunks_were_used(self, session_factory):
        _seed_policy(session_factory, ["Escalation timeout is 30 minutes.", "Unrelated content."])
        agent = PolicyIntelligenceAgent(FakeOllamaClient(default_response="answer"), session_factory)

        result = agent.process({"question": "escalation timeout"})

        assert "1 of 2" in result.reasoning


class TestInputValidation:

    def test_missing_question_is_a_failure(self, session_factory):
        result = PolicyIntelligenceAgent(FakeOllamaClient(), session_factory).process({})

        assert result.success is False
        assert "question" in result.errors[0]

    def test_blank_question_is_a_failure(self, session_factory):
        result = PolicyIntelligenceAgent(FakeOllamaClient(), session_factory).process({"question": "   "})

        assert result.success is False


class TestOllamaFailure:

    def test_an_ollama_error_is_a_failure_not_a_crash(self, session_factory):
        _seed_policy(session_factory, ["Escalation timeout is 30 minutes."])
        from ai.ollama_client import OllamaUnavailableError
        ollama = FakeOllamaClientSequence([OllamaUnavailableError("connection refused")])
        agent = PolicyIntelligenceAgent(ollama, session_factory)

        result = agent.process({"question": "escalation timeout"})

        assert result.success is False
        assert "connection refused" in result.errors[0]


class TestMultipleDocuments:

    def test_retrieval_spans_every_document_in_the_library(self, session_factory):
        with session_factory() as session:
            repo = PolicyRepository(session)
            repo.create_document("Doc A", "a.pdf", UPLOADED_AT, ["Escalation timeout is 30 minutes."])
            repo.create_document("Doc B", "b.pdf", UPLOADED_AT, ["Escalation approvers are managers."])
            session.commit()
        agent = PolicyIntelligenceAgent(FakeOllamaClient(default_response="answer"), session_factory, top_k=5)

        result = agent.process({"question": "escalation"})

        document_ids = {e["document_id"] for e in result.data["excerpts"]}
        assert document_ids == {1, 2}


class TestWithTheRealOllamaClient:
    """
    Everything above uses FakeOllamaClient - fast, and enough to test the
    agent's OWN logic. This class instead wires in the REAL OllamaClient
    (Day 84) against httpx.MockTransport, proving the agent's prompt
    actually flows through the real HTTP client code path end to end,
    not just through a hand-rolled substitute for it.
    """

    def test_the_real_client_sends_the_agents_prompt_and_returns_its_response(self, session_factory):
        import httpx
        from ai.ollama_client import OllamaClient

        _seed_policy(session_factory, ["Escalation timeout is 30 minutes."])
        captured = {}

        def handler(request):
            import json
            captured.update(json.loads(request.content))
            return httpx.Response(200, json={"response": "The timeout is 30 minutes, per [Excerpt 1]."})

        client = OllamaClient("http://localhost:11434", "mistral", transport=httpx.MockTransport(handler))
        agent = PolicyIntelligenceAgent(client, session_factory)

        result = agent.process({"question": "What is the escalation timeout?"})

        assert result.success is True
        assert result.data["answer"] == "The timeout is 30 minutes, per [Excerpt 1]."
        assert "[Excerpt 1] Escalation timeout is 30 minutes." in captured["prompt"]
        assert captured["model"] == "mistral"
