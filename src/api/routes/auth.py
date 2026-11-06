"""User login, rotating refresh sessions and access-token logout."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.api.deps import (
    get_app_settings,
    get_principal,
    get_refresh_token_repository,
    get_token_blocklist_repository,
    get_user_repository,
    require_roles,
)
from src.api.schemas import (
    LogoutRequest,
    RefreshTokenRequest,
    TokenRequest,
    UserCreatePayload,
)
from src.api.security import (
    AuthError,
    Principal,
    Role,
    authenticate_admin,
    create_access_token,
)
from src.config import Settings
from src.repositories.user_repository import (
    AuthenticationError,
    RefreshTokenRepository,
    RefreshTokenReuseError,
    TokenBlocklistRepository,
    UserRepository,
    UserRepositoryError,
    UserView,
)

__all__ = ["router"]

router = APIRouter(prefix="/auth", tags=["auth"])


def _principal_for_user(user: UserView) -> Principal:
    return Principal(sub=user.username, roles=list(user.roles))


def _client_meta(request: Request) -> tuple[str | None, str | None]:
    user_agent = request.headers.get("user-agent")
    host = request.client.host if request.client is not None else None
    return user_agent, host


def _token_response(
    settings: Settings,
    principal: Principal,
    *,
    refresh_token: str | None = None,
) -> dict[str, object]:
    response: dict[str, object] = {
        "access_token": create_access_token(settings, principal.sub, principal.roles),
        "token_type": "bearer",
        "expires_in": settings.jwt_expiry_minutes * 60,
        "roles": [role.value for role in principal.roles],
    }
    if refresh_token is not None:
        response["refresh_token"] = refresh_token
        response["refresh_expires_in"] = settings.refresh_token_expiry_days * 86400
    return response


@router.post("/token")
async def issue_token(
    payload: TokenRequest,
    request: Request,
    settings: Settings = Depends(get_app_settings),
    users: UserRepository = Depends(get_user_repository),
    refresh_tokens: RefreshTokenRepository = Depends(get_refresh_token_repository),
) -> dict[str, object]:
    """Authenticate a user and return a short-lived access token.

    The configured bootstrap admin remains usable for local/emergency access;
    database users additionally receive a durable rotating refresh token.
    """

    user: UserView | None = None
    principal: Principal | None = None
    if payload.username == settings.api_admin_username:
        try:
            principal = authenticate_admin(payload.username, payload.password, settings)
        except AuthError:
            principal = None
    if principal is None:
        try:
            user = await users.authenticate(payload.username, payload.password)
        except AuthenticationError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(exc),
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc
        principal = _principal_for_user(user)

    refresh_token: str | None = None
    if user is not None:
        user_agent, ip_address = _client_meta(request)
        refresh_token, _ = await refresh_tokens.issue(
            user.id,
            ttl_days=settings.refresh_token_expiry_days,
            user_agent=user_agent,
            ip_address=ip_address,
        )
    return _token_response(settings, principal, refresh_token=refresh_token)


@router.post("/refresh")
async def refresh_token(
    payload: RefreshTokenRequest,
    settings: Settings = Depends(get_app_settings),
    users: UserRepository = Depends(get_user_repository),
    refresh_tokens: RefreshTokenRepository = Depends(get_refresh_token_repository),
) -> dict[str, object]:
    """Rotate a refresh token and issue a fresh short-lived access token."""

    try:
        raw_token, rotated = await refresh_tokens.rotate(
            payload.refresh_token,
            ttl_days=settings.refresh_token_expiry_days,
        )
    except RefreshTokenReuseError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="refresh token reuse detected; session family revoked",
        ) from exc
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc

    user = await users.get(rotated.user_id)
    if user is None or not user.is_active:
        await refresh_tokens.revoke_family(rotated.family_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="refresh token user is inactive",
        )
    return _token_response(settings, _principal_for_user(user), refresh_token=raw_token)


@router.post("/logout")
async def logout(
    payload: LogoutRequest,
    settings: Settings = Depends(get_app_settings),
    principal: Principal = Depends(get_principal),
    users: UserRepository = Depends(get_user_repository),
    refresh_tokens: RefreshTokenRepository = Depends(get_refresh_token_repository),
    blocklist: TokenBlocklistRepository = Depends(get_token_blocklist_repository),
) -> dict[str, object]:
    """Revoke the current access JTI and, when supplied, its refresh token."""

    user = await users.get_by_username(principal.sub)
    if principal.jti is not None:
        await blocklist.revoke(
            principal.jti,
            user_id=user.id if user is not None else None,
            expires_at=datetime.now(UTC) + timedelta(minutes=settings.jwt_expiry_minutes),
            reason="logout",
        )
    refresh_revoked = False
    if payload.refresh_token:
        refresh_revoked = await refresh_tokens.revoke(payload.refresh_token)
    return {"status": "logged_out", "refresh_revoked": refresh_revoked}


@router.get("/me")
async def me(
    principal: Principal = Depends(require_roles(Role.ADMIN, Role.SALES, Role.APPROVER, Role.VIEWER)),
) -> dict[str, object]:
    return {
        "subject": principal.sub,
        "roles": [role.value for role in principal.roles],
    }


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreatePayload,
    _: Principal = Depends(require_roles(Role.ADMIN)),
    users: UserRepository = Depends(get_user_repository),
) -> dict[str, object]:
    """Create a database user; only an authenticated admin may do this."""

    try:
        user = await users.create(
            payload.username,
            payload.password,
            payload.roles,
            email=str(payload.email) if payload.email is not None else None,
        )
    except UserRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    return {"user": user.as_dict()}
