# backend/tests/test_api_policy.py

"""
Day 99: the Policy Intelligence routes.

The agent is replaced by a stub (the same dependency-injection seam the
real bootstrap uses), so these tests check the HTTP layer only - who may
call what, validation, error mapping, ownership - with no Ollama, Redis
or policy database. The agent's own logic is covered by test_policy_*.
"""

import pytest

from agents.base_agent import AgentResult
from agents.policy_agent import DEFAULT_MAX_CANDIDATES, DEFAULT_MIN_SHARED_TERMS
from api.routes.policy import MAX_CONFLICT_CANDIDATES
from tests.api_harness import ADMIN_ID, ApiHarness

REQUESTER = "user-003"
OTHER_USER = "user-001"
SUBMISSION = {"resource_id": "resource-001"}
QUESTION = {"question": "How long may a contractor keep access?"}

OLLAMA_DOWN = AgentResult(
    success=False,
    reasoning="Ollama call failed: Cannot reach Ollama at http://internal-host:11434",
    errors=["Cannot reach Ollama at http://internal-host:11434"],
)


class StubPolicyAgent:
    """Records every call and returns canned AgentResults."""

    def __init__(self):
        self.calls = []
        self.ask_result = AgentResult(
            success=True,
            data={
                "answer": "Contractors keep access for 90 days [Excerpt 1].",
                "excerpts": [{"label": "Excerpt 1", "text": "Contractor access lasts 90 days."}],
                "chunks_considered": 4,
                "grounded": True,
                "citations_found": [1],
                "invalid_citations": [],
                "grounding_warning": False,
            },
            reasoning="Retrieved 1 of 4 chunk(s) via keyword overlap",
        )
        self.conflicts_result = AgentResult(
            success=True,
            data={
                "conflicts": [], "candidates_checked": 0, "candidates_skipped": 0,
                "undetermined": 0, "chunks_considered": 4,
            },
            reasoning="No candidate pairs",
        )
        self.explain_failure = None

    def process(self, input_data):
        self.calls.append(("process", input_data))
        return self.ask_result

    def detect_conflicts(self, min_shared_terms=DEFAULT_MIN_SHARED_TERMS, max_candidates=DEFAULT_MAX_CANDIDATES):
        self.calls.append(("detect_conflicts", min_shared_terms, max_candidates))
        return self.conflicts_result

    def explain_decision(self, request_id):
        self.calls.append(("explain_decision", request_id))
        if self.explain_failure is not None:
            return self.explain_failure
        return AgentResult(
            success=True,
            data={
                "request_id": request_id,
                "narrative": "The request was granted because the risk was low.",
                "final_decision": "GRANTED",
                "outcome_consistent": True,
                "records_found": 1,
            },
            reasoning=f"Explained request {request_id}",
        )


@pytest.fixture
def stub():
    return StubPolicyAgent()


@pytest.fixture
def harness(stub):
    harness = ApiHarness()
    harness.app.state.policy_agent = stub
    return harness


@pytest.fixture
def bare_harness():
    """An app built with no policy agent at all."""
    return ApiHarness()


def submit_as_requester(harness) -> str:
    response = harness.as_user(REQUESTER).post("/requests", json=SUBMISSION)
    assert harness.pipeline.audit_records, (
        f"setup failed: no audit record after submitting ({response.status_code}: {response.text})"
    )
    return response.json()["request_id"]


ENDPOINTS = [
    ("post", "/policy/ask", {"json": QUESTION}),
    ("post", "/policy/conflicts", {}),
    ("get", "/policy/explanations/req-x", {}),
]


class TestAuthenticationAndWiring:

    @pytest.mark.parametrize("method,url,kwargs", ENDPOINTS, ids=[f"{m}-{u}" for m, u, _ in ENDPOINTS])
    def test_no_token_is_401(self, harness, stub, method, url, kwargs):
        response = getattr(harness.client, method)(url, **kwargs)

        assert response.status_code == 401
        assert stub.calls == []

    def test_unauthenticated_caller_gets_401_not_503_when_no_agent_is_configured(self, bare_harness):
        assert bare_harness.client.post("/policy/ask", json=QUESTION).status_code == 401

    def test_authenticated_caller_gets_503_when_no_agent_is_configured(self, bare_harness):
        response = bare_harness.as_user(OTHER_USER).post("/policy/ask", json=QUESTION)

        assert response.status_code == 503


class TestAsk:

    def test_any_authenticated_user_can_ask(self, harness, stub):
        response = harness.as_user(OTHER_USER).post("/policy/ask", json=QUESTION)

        assert response.status_code == 200
        body = response.json()
        assert body["answer"].startswith("Contractors keep access")
        assert body["citations_found"] == [1]
        assert body["grounding_warning"] is False
        assert body["reasoning"] == "Retrieved 1 of 4 chunk(s) via keyword overlap"
        assert stub.calls == [("process", QUESTION)]

    @pytest.mark.parametrize("question", ["", "   "])
    def test_blank_question_is_422(self, harness, stub, question):
        response = harness.as_user(OTHER_USER).post("/policy/ask", json={"question": question})

        assert response.status_code == 422
        assert stub.calls == []

    def test_unknown_field_is_422(self, harness, stub):
        response = harness.as_user(OTHER_USER).post("/policy/ask", json={**QUESTION, "role": "CISO"})

        assert response.status_code == 422
        assert stub.calls == []

    def test_grounding_warning_is_passed_through(self, harness, stub):
        stub.ask_result = AgentResult(
            success=True,
            data={
                "answer": "See [Excerpt 7].", "excerpts": [], "chunks_considered": 2, "grounded": True,
                "citations_found": [7], "invalid_citations": [7], "grounding_warning": True,
            },
            reasoning="WARNING: cited nonexistent excerpt(s) [7]",
        )

        body = harness.as_user(OTHER_USER).post("/policy/ask", json=QUESTION).json()

        assert body["grounding_warning"] is True
        assert body["invalid_citations"] == [7]

    def test_ollama_failure_is_503_and_does_not_leak_internals(self, harness, stub):
        stub.ask_result = OLLAMA_DOWN

        response = harness.as_user(OTHER_USER).post("/policy/ask", json=QUESTION)

        assert response.status_code == 503
        assert "internal-host" not in response.text

    def test_other_agent_failure_is_400(self, harness, stub):
        stub.ask_result = AgentResult(success=False, reasoning="Missing required field", errors=["question is required"])

        response = harness.as_user(OTHER_USER).post("/policy/ask", json=QUESTION)

        assert response.status_code == 400


class TestConflicts:

    def test_non_admin_is_403_and_the_agent_is_never_called(self, harness, stub):
        response = harness.as_user(REQUESTER).post("/policy/conflicts")

        assert response.status_code == 403
        assert stub.calls == []

    def test_admin_gets_the_result(self, harness, stub):
        response = harness.as_user(ADMIN_ID).post("/policy/conflicts")

        assert response.status_code == 200
        body = response.json()
        assert body["conflicts"] == []
        assert body["chunks_considered"] == 4

    def test_defaults_are_the_agents_own_defaults(self, harness, stub):
        harness.as_user(ADMIN_ID).post("/policy/conflicts")

        assert stub.calls == [("detect_conflicts", DEFAULT_MIN_SHARED_TERMS, DEFAULT_MAX_CANDIDATES)]

    def test_query_parameters_are_forwarded(self, harness, stub):
        harness.as_user(ADMIN_ID).post("/policy/conflicts", params={"min_shared_terms": 2, "max_candidates": 5})

        assert stub.calls == [("detect_conflicts", 2, 5)]

    @pytest.mark.parametrize(
        "name,value",
        [("max_candidates", 0), ("max_candidates", MAX_CONFLICT_CANDIDATES + 1), ("min_shared_terms", 0)],
    )
    def test_out_of_range_parameters_are_422(self, harness, stub, name, value):
        response = harness.as_user(ADMIN_ID).post("/policy/conflicts", params={name: value})

        assert response.status_code == 422
        assert stub.calls == []


class TestExplanations:

    def test_owner_can_explain_their_own_request(self, harness, stub):
        request_id = submit_as_requester(harness)

        response = harness.as_user(REQUESTER).get(f"/policy/explanations/{request_id}")

        assert response.status_code == 200
        body = response.json()
        assert body["request_id"] == request_id
        assert body["outcome_consistent"] is True
        assert stub.calls == [("explain_decision", request_id)]

    def test_another_user_gets_404_and_the_agent_is_never_called(self, harness, stub):
        request_id = submit_as_requester(harness)

        response = harness.as_user(OTHER_USER).get(f"/policy/explanations/{request_id}")

        assert response.status_code == 404
        assert stub.calls == []

    def test_admin_can_explain_anyones_request(self, harness, stub):
        request_id = submit_as_requester(harness)

        response = harness.as_user(ADMIN_ID).get(f"/policy/explanations/{request_id}")

        assert response.status_code == 200
        assert stub.calls == [("explain_decision", request_id)]

    def test_unknown_request_is_404_even_for_an_admin(self, harness, stub):
        response = harness.as_user(ADMIN_ID).get("/policy/explanations/req-does-not-exist")

        assert response.status_code == 404
        assert stub.calls == []

    def test_ollama_failure_is_503(self, harness, stub):
        request_id = submit_as_requester(harness)
        stub.explain_failure = OLLAMA_DOWN

        response = harness.as_user(REQUESTER).get(f"/policy/explanations/{request_id}")

        assert response.status_code == 503
        assert "internal-host" not in response.text