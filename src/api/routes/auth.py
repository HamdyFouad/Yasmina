"""JWT token issuance for the administrative API user."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.deps import get_app_settings
from src.api.schemas import TokenRequest
from src.api.security import AuthError, authenticate_admin, create_access_token
from src.config import Settings

__all__ = ["router"]

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/token")
async def issue_token(
    payload: TokenRequest,
    settings: Settings = Depends(get_app_settings),
) -> dict[str, object]:
    try:
        principal = authenticate_admin(payload.username, payload.password, settings)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    token = create_access_token(settings, principal.sub, principal.roles)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": settings.jwt_expiry_minutes * 60,
    }
