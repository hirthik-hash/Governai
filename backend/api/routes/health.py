# backend/api/routes/health.py

from fastapi import APIRouter, Depends

from agents.recovery_agent import FailureRecoveryAgent
from api.dependencies import get_pipeline, get_recovery_agent
from api.schemas import EvaluateResponse, HealthCheckItem, SystemHealthResponse
from core.orchestrator import RequestPipeline

router = APIRouter(prefix="/system", tags=["system"])


def _checks(data: dict) -> list[HealthCheckItem]:
    return [
        HealthCheckItem(name=r.check_name, healthy=r.healthy, detail=r.detail)
        for r in data["check_results"]
    ]


@router.get("/health", response_model=SystemHealthResponse)
async def get_health(
    agent: FailureRecoveryAgent = Depends(get_recovery_agent),
    pipeline: RequestPipeline = Depends(get_pipeline),
):
    """
    READ-ONLY. Runs every health check and reports the current
    RecoveryFSM state, but never drives a transition - a GET should
    be safe to poll or retry.
    """
    data = agent.process({}).data
    return SystemHealthResponse(
        system_state=pipeline.recovery_fsm.state.value,
        safe_mode=pipeline.recovery_fsm.is_safe_mode(),
        overall_healthy=data["overall_healthy"],
        checks=_checks(data),
        unhealthy_checks=data["unhealthy_checks"],
    )


@router.post("/health/evaluate", response_model=EvaluateResponse)
async def evaluate_health(
    agent: FailureRecoveryAgent = Depends(get_recovery_agent),
    pipeline: RequestPipeline = Depends(get_pipeline),
):
    """
    Runs the checks AND drives RecoveryFSM (NORMAL -> DEGRADED ->
    SAFE_MODE etc.) via FailureRecoveryAgent.evaluate_and_transition().
    This changes system state, so it is a POST. Unauthenticated until
    Days 76-77 add JWT + RBAC - it should become admin-only then.
    """
    data = agent.evaluate_and_transition().data
    return EvaluateResponse(
        system_state=data["fsm_state"],
        safe_mode=pipeline.recovery_fsm.is_safe_mode(),
        overall_healthy=data["overall_healthy"],
        checks=_checks(data),
        unhealthy_checks=data["unhealthy_checks"],
        fsm_transitioned=data["fsm_transitioned"],
        critical_failure_detected=data["critical_failure_detected"],
    )
