# backend/api/routes/requests.py

from dataclasses import asdict
from uuid import uuid4

from fastapi import APIRouter, Depends, Response

from api.dependencies import get_pipeline
from api.schemas import PendingEscalationsResponse, PipelineResponse, RequestSubmission, ResolveRequest
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

    # Day 70: asked BEFORE submitting so a duplicate can be reported as
    # 409 Conflict. The pipeline itself also refuses duplicates (and
    # supplies the error message), so non-HTTP callers are protected too.
    is_duplicate = pipeline.has_pending_request(data["request_id"])

    result = pipeline.submit_request(data)
    response.status_code = 409 if is_duplicate else STATUS_TO_HTTP.get(result.status, 500)
    return PipelineResponse.from_result(result)


@router.get("/pending", response_model=PendingEscalationsResponse)
async def list_pending(pipeline: RequestPipeline = Depends(get_pipeline)):
    """
    Escalations awaiting a human decision, oldest first. Unauthenticated
    until Days 76-77 - this will become approver/admin-only.
    """
    pending = [asdict(n) for n in pipeline.list_pending_notifications()]
    return PendingEscalationsResponse(count=len(pending), pending=pending)


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
    was_pending = pipeline.has_pending_request(request_id)

    result = pipeline.resolve_escalation(request_id, human_decision=body.decision)
    # Day 70: an id that is not pending (never existed, or already
    # resolved) is 404, distinct from a pending request whose
    # resolution attempt was invalid (400) or blocked (503).
    response.status_code = 404 if not was_pending else STATUS_TO_HTTP.get(result.status, 500)
    return PipelineResponse.from_result(result)
