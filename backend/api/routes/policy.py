# backend/api/routes/policy.py

"""
HTTP access to the Policy Intelligence Agent (Day 99).

ADVISORY ONLY. Nothing here touches RequestPipeline's decision path or
the FSM: these routes ask a language model about policy text and about
decisions that were ALREADY made. The AI advises; the FSM decides.

Who may call what:
  POST /policy/ask                         any authenticated user
  GET  /policy/explanations/{request_id}   the request's owner, or an admin
  POST /policy/conflicts                   admin only - every candidate pair
                                           costs one Ollama call

These are plain `def` routes on purpose: a call can take many seconds
while Ollama generates, and FastAPI runs a plain `def` in its threadpool
instead of freezing the event loop that serializes pipeline requests.
That is safe here because the agent holds no mutable state between calls.
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from agents.base_agent import AgentResult
from agents.policy_agent import DEFAULT_MAX_CANDIDATES, DEFAULT_MIN_SHARED_TERMS, PolicyIntelligenceAgent
from api.dependencies import get_current_principal, get_pipeline, get_policy_agent, require_admin
from api.policy_schemas import (
    DecisionExplanationResponse,
    PolicyAnswerResponse,
    PolicyConflictsResponse,
    PolicyQuestion,
)
from auth.service import Principal
from core.orchestrator import RequestPipeline

router = APIRouter(prefix="/policy", tags=["policy"])

# Each conflict candidate is one Ollama call, so the API bounds how many
# a single admin request may trigger.
MAX_CONFLICT_CANDIDATES = 50


def _raise_for_failure(result: AgentResult) -> None:
    """
    Maps an unsuccessful AgentResult to an HTTP error. The agent's own
    error strings can contain internal addresses (e.g. the Ollama URL), so
    they are logged by the agent but never copied into the response.
    """
    if result.success:
        return
    if result.reasoning.startswith("Ollama call failed"):
        raise HTTPException(status_code=503, detail="The AI model is currently unavailable")
    raise HTTPException(status_code=400, detail="The policy assistant could not process this request")


@router.post(
    "/ask",
    response_model=PolicyAnswerResponse,
    dependencies=[Depends(get_current_principal)],
)
def ask_policy_question(
    body: PolicyQuestion,
    agent: PolicyIntelligenceAgent = Depends(get_policy_agent),
):
    """
    Answers a question from the uploaded policy library. grounding_warning
    is True when the model cited an excerpt that was never retrieved.
    """
    result = agent.process({"question": body.question})
    _raise_for_failure(result)
    return PolicyAnswerResponse(**result.data, reasoning=result.reasoning)


@router.post(
    "/conflicts",
    response_model=PolicyConflictsResponse,
    dependencies=[Depends(require_admin)],
)
def detect_policy_conflicts(
    min_shared_terms: int = Query(DEFAULT_MIN_SHARED_TERMS, ge=1),
    max_candidates: int = Query(DEFAULT_MAX_CANDIDATES, ge=1, le=MAX_CONFLICT_CANDIDATES),
    agent: PolicyIntelligenceAgent = Depends(get_policy_agent),
):
    """Cross-document conflict detection. POST because it triggers real model work."""
    result = agent.detect_conflicts(min_shared_terms=min_shared_terms, max_candidates=max_candidates)
    _raise_for_failure(result)
    return PolicyConflictsResponse(**result.data, reasoning=result.reasoning)


@router.get("/explanations/{request_id}", response_model=DecisionExplanationResponse)
def explain_decision(
    request_id: str,
    principal: Principal = Depends(get_current_principal),
    pipeline: RequestPipeline = Depends(get_pipeline),
    agent: PolicyIntelligenceAgent = Depends(get_policy_agent),
):
    """
    Plain-English narrative of why a request was decided. Ownership comes
    from the audit ledger, never from the client. A request that is not
    yours gets the same 404 as one that does not exist.
    """
    records = [r for r in pipeline.audit_records if r.request_id == request_id]
    allowed = principal.is_admin or (bool(records) and all(r.user_id == principal.user.id for r in records))
    if not records or not allowed:
        raise HTTPException(status_code=404, detail=f"No decision found for request {request_id}")

    result = agent.explain_decision(request_id)
    _raise_for_failure(result)
    return DecisionExplanationResponse(**result.data, reasoning=result.reasoning)