# backend/api/routes/requests.py

from uuid import uuid4

from fastapi import APIRouter, Depends, Response

from api.dependencies import get_pipeline
from api.schemas import PipelineResponse, RequestSubmission, ResolveRequest
from core.orchestrator import RequestPipeline

router = APIRouter(prefix="/requests", tags=["requests"])

# PipelineResult.status -> HTTP status. "denied" is 200 on purpose:
# the request was processed correctly and the governance decision is
# "no" - that is a successful outcome of the API call, not an HTTP
# authorization failure (403 is reserved for API auth, Days 76-77).
STATUS_TO_HTTP = {
    "granted": 200,
    "denied": 200,
    "clarification_needed": 200,
    "pending_approval": 202,
    "blocked_safe_mode": 503,
    "error": 400,
}


@router.post("", response_model=PipelineResponse)
async def submit_request(
    body: RequestSubmission,
    response: Response,
    pipeline: RequestPipeline = Depends(get_pipeline),
):
    # exclude_none: RequestPipeline tests presence with `"role" in
    # request_data`, so an explicit null must not look like a value.
    data = body.model_dump(exclude_none=True)
    # The API layer owns ID generation so a client that omits
    # request_id still gets a stable id to resolve an escalation with.
    data.setdefault("request_id", f"req-{uuid4().hex[:12]}")

    result = pipeline.submit_request(data)
    response.status_code = STATUS_TO_HTTP.get(result.status, 500)
    return PipelineResponse.from_result(result)


@router.post("/{request_id}/resolve", response_model=PipelineResponse)
async def resolve_request(
    request_id: str,
    body: ResolveRequest,
    response: Response,
    pipeline: RequestPipeline = Depends(get_pipeline),
):
    """
    Completes a request parked as pending_approval. Unauthenticated
    until Days 76-77: today ANYONE can approve - the approver's
    identity is not verified. Known gap, not an oversight.
    """
    result = pipeline.resolve_escalation(request_id, human_decision=body.decision)
    response.status_code = STATUS_TO_HTTP.get(result.status, 500)
    return PipelineResponse.from_result(result)
