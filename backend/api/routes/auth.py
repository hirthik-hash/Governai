# backend/api/routes/auth.py

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_auth_service, get_current_principal
from api.schemas import CurrentUserResponse, LoginRequest, TokenResponse
from auth.errors import AuthenticationError
from auth.service import AuthService, Principal

router = APIRouter(prefix="/auth", tags=["auth"])


# Plain `def`, not async: Argon2 verification is deliberately slow (tens
# to hundreds of milliseconds of CPU), so FastAPI runs it in its thread
# pool instead of blocking the event loop that serves every other request.
@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, auth_service: AuthService = Depends(get_auth_service)):
    try:
        issued = auth_service.login(body.user_id, body.password)
    except AuthenticationError:
        raise HTTPException(status_code=401, detail="Invalid credentials", headers={"WWW-Authenticate": "Bearer"})
    return TokenResponse(access_token=issued.access_token, expires_in=issued.expires_in_seconds)


@router.get("/me", response_model=CurrentUserResponse)
def me(principal: Principal = Depends(get_current_principal)):
    # Deliberately not returned: blacklist status and reporting line.
    user = principal.user
    return CurrentUserResponse(
        user_id=user.id,
        name=user.name,
        department=user.department,
        role=user.role,
        clearance_level=user.clearance_level,
        api_role=principal.role,
    )
