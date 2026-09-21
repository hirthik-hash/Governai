# backend/api/dependencies.py

"""
FastAPI dependencies. Routes never construct a RequestPipeline,
FailureRecoveryAgent or AuthService themselves - they receive the one
shared instance that create_app() put on app.state. This is the same
constructor-injection pattern used everywhere else in the project,
applied at the HTTP boundary.

Authentication and authorization (Days 76-77):
  get_current_principal - who is calling (401 if nobody valid);
  require_admin         - the caller must also hold the admin API role (403).
Authentication is resolved before the request body is validated, so an
unauthenticated caller always gets 401, never a hint about the body.
"""

from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from agents.recovery_agent import FailureRecoveryAgent
from auth.errors import AuthenticationError
from auth.service import AuthService, Principal
from core.orchestrator import RequestPipeline

# auto_error=False so a missing or malformed header reaches
# get_current_principal, which answers with one consistent 401.
_bearer_scheme = HTTPBearer(auto_error=False)

_UNAUTHENTICATED = {"WWW-Authenticate": "Bearer"}


def get_pipeline(request: Request) -> RequestPipeline:
    return request.app.state.pipeline


def get_recovery_agent(request: Request) -> FailureRecoveryAgent:
    return request.app.state.recovery_agent


def get_auth_service(request: Request) -> AuthService:
    return request.app.state.auth_service


def get_current_principal(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    auth_service: AuthService = Depends(get_auth_service),
) -> Principal:
    """
    The authenticated caller, from the Authorization: Bearer header.
    Every failure - no header, wrong scheme, bad signature, expired,
    user gone - is the same 401 with the same body.

    Plain `def`, not async: verifying a token reads the directory and
    the role store (database sessions), which should not run on the
    event loop.
    """
    if credentials is None:
        raise HTTPException(status_code=401, detail="Not authenticated", headers=_UNAUTHENTICATED)
    try:
        return auth_service.authenticate_principal(credentials.credentials)
    except AuthenticationError:
        raise HTTPException(status_code=401, detail="Not authenticated", headers=_UNAUTHENTICATED)


def require_admin(principal: Principal = Depends(get_current_principal)) -> Principal:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="Forbidden")
    return principal
