# backend/agents/base_agent.py

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from core.app_logger import get_logger


@dataclass
class AgentResult:
    """
    Standard output shape every agent returns, regardless of what
    it actually does internally. This uniformity is what lets the
    orchestrator (Phase 2, Days 60-61) and later the Explainability
    Center treat all 7 agents the same way.
    """
    success: bool
    data: dict = field(default_factory=dict)
    reasoning: str = ""
    errors: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def merge_into_context(self, context: dict) -> dict:
        """
        Merges this agent's output data into an existing FSM context
        dict, returning a new dict (does not mutate the original).
        This is the bridge between 'what an agent decided' and 'what
        the FSM needs to see' - every agent's data becomes part of
        the next FSM transition's context.
        """
        merged = dict(context)
        merged.update(self.data)
        return merged


class BaseAgent(ABC):
    """
    Abstract base for all 7 GovernAI agents. Each agent takes some
    input, reasons about it, and returns an AgentResult - never
    raises for expected/handled cases (use AgentResult.success=False
    with errors populated instead), so the orchestrator never needs
    a try/except around every agent call.
    """

    def __init__(self):
        self.logger = get_logger(f"agents.{self.agent_name}")

    @property
    @abstractmethod
    def agent_name(self) -> str:
        """Short identifier used in logs, e.g. 'request_understanding'."""
        raise NotImplementedError

    @abstractmethod
    def process(self, input_data: dict) -> AgentResult:
        """
        Performs this agent's specific reasoning over input_data and
        returns a structured AgentResult. Must not raise for expected
        failure cases - only for genuine programming errors.
        """
        raise NotImplementedError

    def _success(self, data: dict, reasoning: str) -> AgentResult:
        self.logger.info(f"{self.agent_name} succeeded: {reasoning}")
        return AgentResult(success=True, data=data, reasoning=reasoning)

    def _failure(self, reasoning: str, errors: list[str] = None) -> AgentResult:
        errors = errors or []
        self.logger.warning(f"{self.agent_name} failed: {reasoning} | errors={errors}")
        return AgentResult(success=False, data={}, reasoning=reasoning, errors=errors)