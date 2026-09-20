# backend/api/schemas.py

"""
Request/response models for the HTTP layer (Day 69).

These are deliberately thin: they validate the SHAPE of incoming JSON
and serialize results. All governance logic stays in RequestPipeline
and the agents - nothing here decides anything.
"""

from dataclasses import asdict
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from core.orchestrator import PipelineResult


class RequestSubmission(BaseModel):
    """
    Body of POST /requests. Only user_id is required at the HTTP
    layer; everything else is optional because RequestPipeline already
    validates and reports missing/invalid fields as a clean "error"
    result (Day 64's robustness pass) - one source of truth.
    """
    user_id: str
    request_id: Optional[str] = None
    resource_id: Optional[str] = None
    resource_name: Optional[str] = None
    session_token: Optional[str] = None
    session_expired: Optional[bool] = None
    urgency: Optional[str] = None
    role: Optional[str] = None
    location: Optional[str] = None
    is_public_readonly: Optional[bool] = None


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
