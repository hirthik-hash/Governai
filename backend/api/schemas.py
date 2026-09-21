# backend/api/schemas.py

"""
Request/response models for the HTTP layer (Day 69).

These are deliberately thin: they validate the SHAPE of incoming JSON
and serialize results. All governance logic stays in RequestPipeline
and the agents - nothing here decides anything.
"""

from dataclasses import asdict
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from core.orchestrator import PipelineResult


class RequestSubmission(BaseModel):
    """
    Body of POST /requests. Anything the server can know for itself is NOT
    accepted from the client, and unknown fields are rejected (422) rather
    than silently ignored, so an old or hostile client finds out:

      user_id            optional; if sent it must equal the token's user.
      session_token      the server uses the caller's own bearer token.
      role               the server uses the caller's job title from the
                         directory. (Accepting it from the client would let
                         anyone claim "CISO" and bypass clearance checks.)
      session_expired    a client-controlled flag; sessions are real tokens now.
      is_public_readonly a client-controlled flag that bypasses the safe-mode
                         gate; it stays out of the API until a policy defines
                         which requests genuinely qualify.

    location is still accepted: it is a self-reported risk signal, not an
    identity or an entitlement, and cannot be verified server-side yet.
    """
    model_config = ConfigDict(extra="forbid")

    user_id: Optional[str] = None
    request_id: Optional[str] = None
    resource_id: Optional[str] = None
    resource_name: Optional[str] = None
    urgency: Optional[str] = None
    location: Optional[str] = None


class ResolveRequest(BaseModel):
    """
    Body of POST /requests/{request_id}/resolve. decision=None means
    "no human decision yet - check the timeout" (same semantics as
    RequestPipeline.resolve_escalation()).
    """
    decision: Optional[Literal["approved", "rejected"]] = None


class PipelineResponse(BaseModel):
    request_id: str
    status: str
    fsm_state: str = ""
    candidate_resource_ids: list[str] = Field(default_factory=list)
    notification: Optional[dict[str, Any]] = None
    audit_record: Optional[dict[str, Any]] = None
    errors: list[str] = Field(default_factory=list)

    @classmethod
    def from_result(cls, result: PipelineResult) -> "PipelineResponse":
        return cls(
            request_id=result.request_id,
            status=result.status,
            fsm_state=result.fsm_state,
            candidate_resource_ids=list(result.candidate_resource_ids),
            notification=asdict(result.notification) if result.notification is not None else None,
            audit_record=asdict(result.audit_record) if result.audit_record is not None else None,
            errors=list(result.errors),
        )


class HealthCheckItem(BaseModel):
    name: str
    healthy: bool
    detail: str


class SystemHealthResponse(BaseModel):
    system_state: str
    safe_mode: bool
    overall_healthy: bool
    checks: list[HealthCheckItem]
    unhealthy_checks: list[str]


class EvaluateResponse(SystemHealthResponse):
    fsm_transitioned: bool
    critical_failure_detected: bool


class PendingEscalationsResponse(BaseModel):
    count: int
    pending: list[dict[str, Any]]


class AuditListResponse(BaseModel):
    count: int
    records: list[dict[str, Any]]


class AuditSummaryResponse(BaseModel):
    total_requests: int
    decision_breakdown: dict[str, int]
    average_risk_score: float
    escalation_rate: float


class LoginRequest(BaseModel):
    # Length caps stop a client from making the server hash or store huge input.
    user_id: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class CurrentUserResponse(BaseModel):
    user_id: str
    name: str
    department: str
    role: str
    clearance_level: int
    api_role: str
