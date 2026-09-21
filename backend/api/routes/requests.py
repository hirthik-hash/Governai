# backend/api/routes/requests.py

from dataclasses import asdict
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response

from api.dependencies import get_current_principal, get_pipeline
from api.schemas import PendingEscalationsResponse, PipelineResponse, RequestSubmission, ResolveRequest
from auth.service import Principal
from core.orchestrator import RequestPipeline

router = APIRouter(prefix="/requests", tags=["requests"])

# PipelineResult.status -> HTTP status. "denied" is 200 on purpose:
# the request was processed correctly and the governance decision is
# "no" - that is a successful outcome of the API call, not an HTTP
# authorization failure (403 is reserved for API permissions).
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
    principal: Principal = Depends(get_current_principal),
    pipeline: RequestPipeline = Depends(get_pipeline),
):
    """
    Submits an access request AS THE AUTHENTICATED USER. Nobody - admins
    included - can submit on behalf of someone else: the governance
    decision and its audit record must be about the person asking.
    """
    if body.user_id is not None and body.user_id != principal.user.id:
        raise HTTPException(status_code=403, detail="Requests can only be submitted as yourself")

    # exclude_none: RequestPipeline tests presence with `"key" in
    # request_data`, so an explicit null must not look like a value.
    data = body.model_dump(exclude_none=True)
    data["user_id"] = principal.user.id
    # The caller's own token; the pipeline's JwtSessionValidator re-verifies it.
    data["session_token"] = principal.token
    # From the directory, never from the client (see RequestSubmission).
    data["role"] = principal.user.role
    # The API layer owns ID generation so a client that omits
    # request_id still gets a stable id to resolve an escalation with.
    data.setdefault("request_id", f"req-{uuid4().hex[:12]}")

    # Asked BEFORE submitting so a duplicate can be reported as
    # 409 Conflict. The pipeline itself also refuses duplicates (and
    # supplies the error message), so non-HTTP callers are protected too.
    is_duplicate = pipeline.has_pending_request(data["request_id"])

    result = pipeline.submit_request(data)
    response.status_code = 409 if is_duplicate else STATUS_TO_HTTP.get(result.status, 500)
    return PipelineResponse.from_result(result)


@router.get("/pending", response_model=PendingEscalationsResponse)
async def list_pending(
    principal: Principal = Depends(get_current_principal),
    pipeline: RequestPipeline = Depends(get_pipeline),
):
    """
    Escalations awaiting a human decision, oldest first. An approver
    sees only the ones assigned to them; an admin sees all of them.
    """
    notifications = pipeline.list_pending_notifications()
    if not principal.is_admin:
        notifications = [n for n in notifications if n.approver_user_id == principal.user.id]
    pending = [asdict(n) for n in notifications]
    return PendingEscalationsResponse(count=len(pending), pending=pending)


@router.post("/{request_id}/resolve", response_model=PipelineResponse)
async def resolve_request(
    request_id: str,
    body: ResolveRequest,
    response: Response,
    principal: Principal = Depends(get_current_principal),
    pipeline: RequestPipeline = Depends(get_pipeline),
):
    """
    Completes a request parked as pending_approval. Only the approver the
    escalation was routed to may decide it (an admin may additionally run
    the timeout check, decision=null). Anyone else gets the same 404 as
    for an id that does not exist, so pending request ids are not revealed.
    """
    notification = pipeline.get_pending_notification(request_id)
    may_resolve = notification is not None and (
        notification.approver_user_id == principal.user.id
        or (body.decision is None and principal.is_admin)
    )
    if not may_resolve:
        response.status_code = 404
        return PipelineResponse(
            request_id=request_id, status="error", errors=[f"No pending request {request_id}"],
        )

    result = pipeline.resolve_escalation(request_id, human_decision=body.decision)
    response.status_code = STATUS_TO_HTTP.get(result.status, 500)
    return PipelineResponse.from_result(result)
