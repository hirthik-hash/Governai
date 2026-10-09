# backend/api/policy_schemas.py

"""
Request/response models for the Policy Intelligence routes (Day 99).

Thin on purpose, like api/schemas.py: they validate the SHAPE of the
JSON and serialize AgentResult.data. Every field below mirrors a key the
agent documents in agents/policy_agent.py; nothing here interprets it.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PolicyQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # max_length bounds prompt size (and so Ollama cost); the pattern
    # rejects a question made only of whitespace.
    question: str = Field(min_length=1, max_length=1000, pattern=r"\S")


class PolicyAnswerResponse(BaseModel):
    answer: str = ""
    excerpts: list[dict[str, Any]] = Field(default_factory=list)
    chunks_considered: int = 0
    grounded: bool = False
    citations_found: list[int] = Field(default_factory=list)
    invalid_citations: list[int] = Field(default_factory=list)
    grounding_warning: bool = False
    reasoning: str = ""


class PolicyConflictsResponse(BaseModel):
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    candidates_checked: int = 0
    candidates_skipped: int = 0
    undetermined: int = 0
    chunks_considered: int = 0
    reasoning: str = ""


class DecisionExplanationResponse(BaseModel):
    request_id: str
    narrative: str
    final_decision: str
    outcome_consistent: bool
    records_found: int = 1
    reasoning: str = ""