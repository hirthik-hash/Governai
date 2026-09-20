# backend/api/dependencies.py

"""
FastAPI dependencies. Routes never construct a RequestPipeline or
FailureRecoveryAgent themselves - they receive the one shared
instance that create_app() put on app.state. This is the same
constructor-injection pattern used everywhere else in the project,
applied at the HTTP boundary.
"""

from fastapi import Request

from agents.recovery_agent import FailureRecoveryAgent
from core.orchestrator import RequestPipeline


def get_pipeline(request: Request) -> RequestPipeline:
    return request.app.state.pipeline


def get_recovery_agent(request: Request) -> FailureRecoveryAgent:
    return request.app.state.recovery_agent
