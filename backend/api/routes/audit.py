# backend/api/routes/audit.py

"""
Read-only views over the AuditComplianceAgent's records (Day 70).
All filtering, sorting and summarizing is delegated to the agent's own
functions (Days 50-52) - this layer only translates query parameters.
"""

from dataclasses import asdict
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query

from agents.audit_agent import filter_records, generate_summary, sort_records
from api.dependencies import get_pipeline
from api.schemas import AuditListResponse, AuditSummaryResponse
from core.orchestrator import RequestPipeline

router = APIRouter(prefix="/audit", tags=["audit"])

# Only real AuditRecord fields the API is willing to sort by. Anything
# else is a 422 at the boundary; sort_records() itself still raises on
# a bad field name, which stays a loud programming error inside.
SortField = Literal["timestamp", "request_id", "user_id", "risk_score", "risk_level", "final_decision"]


def _filtered(pipeline, user_id, final_decision, risk_level, start_date, end_date):
    return filter_records(
        pipeline.audit_records,
        user_id=user_id,
        final_decision=final_decision,
        risk_level=risk_level,
        start_date=start_date,
        end_date=end_date,
    )


@router.get("", response_model=AuditListResponse)
async def list_audit_records(
    user_id: Optional[str] = None,
    final_decision: Optional[str] = None,
    risk_level: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    sort_by: SortField = "timestamp",
    descending: bool = False,
    pipeline: RequestPipeline = Depends(get_pipeline),
):
    records = sort_records(
        _filtered(pipeline, user_id, final_decision, risk_level, start_date, end_date),
        by=sort_by,
        descending=descending,
    )
    return AuditListResponse(count=len(records), records=[asdict(r) for r in records])


@router.get("/summary", response_model=AuditSummaryResponse)
async def audit_summary(
    user_id: Optional[str] = None,
    final_decision: Optional[str] = None,
    risk_level: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    pipeline: RequestPipeline = Depends(get_pipeline),
):
    """Rule-based aggregate over the same filters as GET /audit."""
    records = _filtered(pipeline, user_id, final_decision, risk_level, start_date, end_date)
    return AuditSummaryResponse(**generate_summary(records))
