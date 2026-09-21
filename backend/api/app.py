# backend/api/app.py

"""
Application factory (Day 69; auth Day 76; role-based access Day 77).

create_app() builds ONE RequestPipeline and ONE FailureRecoveryAgent
per app instance and shares them across every request via app.state.
It has to be one shared pipeline, not one per HTTP request: the
pipeline holds stateful trackers (request history, geo history,
escalation timeouts) and the pending-escalation store, and all of
that would be silently reset on every call otherwise.

All three collaborators are injectable so tests (and the real wiring in
api/bootstrap.py) can pass their own. If you pass a custom
recovery_agent, it must drive the SAME RecoveryFSM as the pipeline
(recovery_fsm=pipeline.recovery_fsm), otherwise the health endpoints and
the safe-mode gate would disagree.

With no auth_service the app gets one whose credential store is EMPTY,
so nobody can log in: the safe default when nothing real is configured.
The real wiring (JWT_SECRET_KEY from settings, database credentials) is
in api/bootstrap.py.

Concurrency: the request endpoints are `async def` and the pipeline is
synchronous, so requests run one at a time on the event loop - that
serialization is what keeps the shared in-memory state race-free.
Run a SINGLE uvicorn worker until state moves to Redis/DB (Days 80-81).
"""

import secrets

from fastapi import FastAPI

from agents.recovery_agent import FailureRecoveryAgent
from api.routes.audit import router as audit_router
from api.routes.auth import router as auth_router
from api.routes.health import public_router as public_health_router
from api.routes.health import router as health_router
from api.routes.requests import router as requests_router
from auth.credentials import InMemoryCredentialStore
from auth.passwords import PasswordService
from auth.service import AuthService
from auth.tokens import TokenService
from core.orchestrator import RequestPipeline


def create_app(
    pipeline: RequestPipeline = None,
    recovery_agent: FailureRecoveryAgent = None,
    auth_service: AuthService = None,
) -> FastAPI:
    app = FastAPI(
        title="GovernAI",
        version="0.3.0-dev",
        description="FSM-governed access decisions. AI advises; the FSM decides.",
    )

    app.state.pipeline = pipeline or RequestPipeline()
    app.state.recovery_agent = recovery_agent or FailureRecoveryAgent(
        recovery_fsm=app.state.pipeline.recovery_fsm
    )
    app.state.auth_service = auth_service or AuthService(
        credentials=InMemoryCredentialStore(),
        directory=app.state.pipeline.directory,
        passwords=PasswordService(),
        # A throwaway per-process secret: with an empty credential store no
        # token is ever issued, so this never needs to match any configuration
        # (and a bad JWT_SECRET_KEY in .env cannot break create_app()).
        tokens=TokenService(secrets.token_urlsafe(48)),
    )

    app.include_router(public_health_router)
    app.include_router(auth_router)
    app.include_router(health_router)
    app.include_router(requests_router)
    app.include_router(audit_router)
    return app
