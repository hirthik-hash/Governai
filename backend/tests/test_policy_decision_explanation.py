# backend/tests/test_policy_decision_explanation.py

"""Days 96-97: PolicyIntelligenceAgent.explain_decision(), against a real in-memory database and the fake Ollama clients."""

import pytest

from agents.audit_agent import AuditRecord
from agents.policy_agent import PolicyIntelligenceAgent, _check_outcome_consistency
from ai.fake_ollama_client import FakeOllamaClient, FakeOllamaClientSequence
from database.repositories import AuditRecordRepository
from database.seeding import seed_database
from database.session import init_db, make_engine, make_session_factory


@pytest.fixture
def session_factory():
    engine = make_engine("sqlite://")
    init_db(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        seed_database(session)  # audit_records.user_id/resource_id are real foreign keys
        session.commit()
    return factory


def _record(request_id="req-1", **overrides) -> AuditRecord:
    fields = dict(
        request_id=request_id, timestamp="2026-09-28T10:00:00+00:00", user_id="user-001",
        resource_id="resource-001", action_requested="access request for resource-001",
        final_fsm_state="closed", risk_score=45, risk_level="HIGH", final_decision="GRANTED",
        approver_user_id="", agent_reasoning_trail=["Clearance sufficient.", "Risk acceptable.", "Access granted."],
        policy_rule_cited=None,
    )
    fields.update(overrides)
    return AuditRecord(**fields)


def _seed_record(session_factory, record: AuditRecord) -> None:
    with session_factory() as session:
        AuditRecordRepository(session).add(record)
        session.commit()


class TestCheckOutcomeConsistency:

    def test_granted_narrative_with_grant_language_is_consistent(self):
        assert _check_outcome_consistency("GRANTED", "The request was approved based on sufficient clearance.") is True

    def test_granted_narrative_that_claims_denial_is_inconsistent(self):
        assert _check_outcome_consistency("GRANTED", "The request was denied due to insufficient clearance.") is False

    def test_denied_narrative_with_deny_language_is_consistent(self):
        assert _check_outcome_consistency("DENIED", "The request was rejected due to high risk.") is True

    def test_denied_narrative_that_claims_grant_is_inconsistent(self):
        assert _check_outcome_consistency("DENIED", "The request was granted after review.") is False

    def test_granted_narrative_mentioning_a_risk_that_was_overcome_is_still_consistent(self):
        # Mentions "denied" in passing (e.g. "would have been denied but...")
        # while ALSO confirming the real outcome - not a contradiction.
        narrative = "Although risk was high enough that denial was considered, the request was ultimately granted."
        assert _check_outcome_consistency("GRANTED", narrative) is True

    def test_pending_narrative_claiming_a_grant_is_inconsistent(self):
        assert _check_outcome_consistency("PENDING", "The request was granted after manager approval.") is False

    def test_pending_narrative_claiming_a_denial_is_inconsistent(self):
        assert _check_outcome_consistency("PENDING", "The request was denied.") is False

    def test_pending_narrative_with_neither_word_is_consistent(self):
        assert _check_outcome_consistency("PENDING", "The request is still awaiting a decision from the approver.") is True

    def test_is_case_insensitive(self):
        assert _check_outcome_consistency("GRANTED", "THE REQUEST WAS APPROVED.") is True


class TestExplainDecision:

    def test_no_record_for_the_request_id_is_a_failure(self, session_factory):
        agent = PolicyIntelligenceAgent(FakeOllamaClient(), session_factory)

        result = agent.explain_decision("req-does-not-exist")

        assert result.success is False
        assert "req-does-not-exist" in result.errors[0]

    def test_a_granted_decision_is_explained_and_marked_consistent(self, session_factory):
        _seed_record(session_factory, _record("req-1", final_decision="GRANTED"))
        ollama = FakeOllamaClient(default_response="The request was granted because the risk was acceptable.")
        agent = PolicyIntelligenceAgent(ollama, session_factory)

        result = agent.explain_decision("req-1")

        assert result.success is True
        assert result.data["narrative"] == "The request was granted because the risk was acceptable."
        assert result.data["final_decision"] == "GRANTED"
        assert result.data["outcome_consistent"] is True

    def test_a_hallucinated_contradiction_is_flagged(self, session_factory):
        _seed_record(session_factory, _record("req-1", final_decision="GRANTED"))
        ollama = FakeOllamaClient(default_response="Unfortunately, the request was denied due to excessive risk.")
        agent = PolicyIntelligenceAgent(ollama, session_factory)

        result = agent.explain_decision("req-1")

        assert result.success is True  # Ollama call itself succeeded - the WARNING is data, not a crash
        assert result.data["outcome_consistent"] is False
        assert "WARNING" in result.reasoning

    def test_the_prompt_includes_the_real_reasoning_trail_and_outcome(self, session_factory):
        _seed_record(session_factory, _record(
            "req-1", final_decision="DENIED",
            agent_reasoning_trail=["Clearance insufficient.", "Blacklist match.", "Access denied."],
        ))
        ollama = FakeOllamaClient(default_response="answer")
        agent = PolicyIntelligenceAgent(ollama, session_factory)

        agent.explain_decision("req-1")

        prompt = ollama.calls[0]["prompt"]
        assert "Clearance insufficient." in prompt
        assert "Blacklist match." in prompt
        assert "Final recorded outcome: DENIED" in prompt

    def test_the_system_prompt_forbids_inventing_reasons(self, session_factory):
        _seed_record(session_factory, _record("req-1"))
        ollama = FakeOllamaClient(default_response="answer")
        agent = PolicyIntelligenceAgent(ollama, session_factory)

        agent.explain_decision("req-1")

        assert "do not invent" in ollama.calls[0]["system"].lower()

    def test_the_most_recent_record_is_explained_when_several_exist(self, session_factory):
        _seed_record(session_factory, _record("req-1", final_decision="PENDING", agent_reasoning_trail=["Escalated."]))
        _seed_record(session_factory, _record("req-1", final_decision="GRANTED", agent_reasoning_trail=["Escalated.", "Approved by manager."]))
        ollama = FakeOllamaClient(default_response="answer")
        agent = PolicyIntelligenceAgent(ollama, session_factory)

        result = agent.explain_decision("req-1")

        assert result.data["final_decision"] == "GRANTED"
        assert result.data["records_found"] == 2

    def test_no_approver_is_rendered_clearly_not_as_an_empty_string(self, session_factory):
        _seed_record(session_factory, _record("req-1", approver_user_id=""))
        ollama = FakeOllamaClient(default_response="answer")
        agent = PolicyIntelligenceAgent(ollama, session_factory)

        agent.explain_decision("req-1")

        assert "not escalated" in ollama.calls[0]["prompt"].lower()

    def test_an_approver_is_included_when_the_request_was_escalated(self, session_factory):
        _seed_record(session_factory, _record("req-1", approver_user_id="user-007"))
        ollama = FakeOllamaClient(default_response="answer")
        agent = PolicyIntelligenceAgent(ollama, session_factory)

        agent.explain_decision("req-1")

        assert "user-007" in ollama.calls[0]["prompt"]

    def test_an_ollama_error_is_a_failure_not_a_crash(self, session_factory):
        from ai.ollama_client import OllamaUnavailableError
        _seed_record(session_factory, _record("req-1"))
        ollama = FakeOllamaClientSequence([OllamaUnavailableError("connection refused")])
        agent = PolicyIntelligenceAgent(ollama, session_factory)

        result = agent.explain_decision("req-1")

        assert result.success is False
        assert "connection refused" in result.errors[0]
