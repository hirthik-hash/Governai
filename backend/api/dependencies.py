# backend/api/dependencies.py

"""
FastAPI dependencies. Routes never construct a RequestPipeline,
FailureRecoveryAgent or AuthService themselves - they receive the one
shared instance that create_app() put on app.state. This is the same
constructor-injection pattern used everywhere else in the project,
applied at the HTTP boundary.
"""

from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from agents.recovery_agent import FailureRecoveryAgent
from auth.errors import AuthenticationError
from auth.service import AuthService
from core.orchestrator import RequestPipeline
from data.seed_data import User

# auto_error=False so a missing or malformed header reaches
# get_current_user, which answers with one consistent 401.
_bearer_scheme = HTTPBearer(auto_error=False)

_UNAUTHENTICATED = {"WWW-Authenticate": "Bearer"}


def get_pipeline(request: Request) -> RequestPipeline:
    return request.app.state.pipeline


def get_recovery_agent(request: Request) -> FailureRecoveryAgent:
    return request.app.state.recovery_agent


def get_auth_service(request: Request) -> AuthService:
    return request.app.state.auth_service


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    auth_service: AuthService = Depends(get_auth_service),
) -> User:
    """
    The authenticated user, from the Authorization: Bearer header.
    Every failure - no header, wrong scheme, bad signature, expired,
    user gone - is the same 401 with the same body.

    Plain `def`, not async: verifying a token reads the directory (a
    database session), which should not run on the event loop.
    """
    if credentials is None:
        raise HTTPException(status_code=401, detail="Not authenticated", headers=_UNAUTHENTICATED)
    try:
        return auth_service.authenticate_token(credentials.credentials)
    except AuthenticationError:
        raise HTTPException(status_code=401, detail="Not authenticated", headers=_UNAUTHENTICATED)
