# backend/tests/test_base_agent.py

import pytest
from agents.base_agent import BaseAgent, AgentResult


class DummyAgent(BaseAgent):
    """Minimal concrete agent, used only to test BaseAgent's shared behavior."""

    @property
    def agent_name(self) -> str:
        return "dummy"

    def process(self, input_data: dict) -> AgentResult:
        if input_data.get("should_fail"):
            return self._failure("input flagged for failure", errors=["should_fail was True"])
        return self._success({"processed": True}, "dummy processing succeeded")


class TestBaseAgentCannotBeInstantiatedDirectly:

    def test_instantiating_base_agent_raises_type_error(self):
        with pytest.raises(TypeError):
            BaseAgent()


class TestAgentResultShape:

    def test_success_result_has_expected_fields(self):
        agent = DummyAgent()
        result = agent.process({"should_fail": False})

        assert result.success is True
        assert result.data == {"processed": True}
        assert result.reasoning == "dummy processing succeeded"
        assert result.errors == []
        assert result.timestamp  # non-empty

    def test_failure_result_has_expected_fields(self):
        agent = DummyAgent()
        result = agent.process({"should_fail": True})

        assert result.success is False
        assert result.data == {}
        assert "should_fail was True" in result.errors

    def test_agent_never_raises_for_expected_failure_case(self):
        agent = DummyAgent()
        # Should not raise, even though should_fail=True is a "bad" input
        result = agent.process({"should_fail": True})
        assert isinstance(result, AgentResult)


class TestMergeIntoContext:

    def test_merge_adds_new_keys_without_mutating_original(self):
        agent = DummyAgent()
        result = agent.process({"should_fail": False})

        original_context = {"clearance": 3, "risk_score": 10}
        merged = result.merge_into_context(original_context)

        assert merged == {"clearance": 3, "risk_score": 10, "processed": True}
        assert original_context == {"clearance": 3, "risk_score": 10}  # unchanged

    def test_merge_overwrites_conflicting_keys(self):
        agent = DummyAgent()
        result = AgentResult(success=True, data={"risk_score": 99}, reasoning="test")

        merged = result.merge_into_context({"risk_score": 10, "clearance": 3})

        assert merged["risk_score"] == 99
        assert merged["clearance"] == 3


class TestAgentNameIsUsedInLogging:

    def test_agent_name_property_is_accessible(self):
        agent = DummyAgent()
        assert agent.agent_name == "dummy"