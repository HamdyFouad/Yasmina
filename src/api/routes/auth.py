"""Account lifecycle: login with TOTP MFA, password reset and session revocation."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from src.api.deps import (
    get_app_settings,
    get_audit_service,
    get_optional_principal,
    get_password_reset_repository,
    get_principal,
    get_refresh_token_repository,
    get_token_blocklist_repository,
    get_user_repository,
    require_roles,
)
from src.api.schemas import (
    LogoutRequest,
    MfaCodePayload,
    MfaDisablePayload,
    PasswordChangePayload,
    PasswordResetPayload,
    PasswordResetRequestPayload,
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
from src.integrations.telegram_notifier import TelegramNotifier
from src.models.enums import AuditEventType
from src.repositories.user_repository import (
    AuthenticationError,
    PasswordResetTokenError,
    PasswordResetTokenRepository,
    RefreshTokenRepository,
    RefreshTokenReuseError,
    TokenBlocklistRepository,
    UserRepository,
    UserRepositoryError,
    UserView,
)
from src.services.audit_service import AuditService
from src.services.mfa_service import (
    MfaConfigurationError,
    build_otpauth_uri,
    decrypt_secret,
    encrypt_secret,
    generate_secret,
    verify_totp,
)
from src.services.password_service import PasswordHashError, validate_password

__all__ = ["router"]

logger = logging.getLogger("yasmina.auth")

router = APIRouter(prefix="/auth", tags=["auth"])

_MFA_HEADERS = {"X-Yasmina-MFA-Required": "true", "WWW-Authenticate": "Bearer"}


def _principal_for_user(user: UserView) -> Principal:
    return Principal(sub=user.username, roles=list(user.roles))


def _client_meta(request: Request) -> tuple[str | None, str | None]:
    user_agent = request.headers.get("user-agent")
    host = request.client.host if request.client is not None else None
    return user_agent, host


async def _audit(
    audit: AuditService,
    event: AuditEventType,
    *,
    actor: str,
    entity_id: str,
    payload: dict[str, Any],
) -> None:
    await audit.append(
        event_type=event,
        actor=actor[:128],
        entity_type="user",
        entity_id=entity_id[:64],
        payload=payload,
    )


def _token_response(
    settings: Settings,
    principal: Principal,
) -> dict[str, object]:
    """Access-token payload; the refresh token only travels in the HttpOnly cookie."""

    return {
        "access_token": create_access_token(settings, principal.sub, principal.roles),
        "token_type": "bearer",
        "expires_in": settings.jwt_expiry_minutes * 60,
        "roles": [role.value for role in principal.roles],
    }


def _set_refresh_cookie(response: Response, settings: Settings, raw_token: str) -> None:
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=raw_token,
        max_age=settings.refresh_token_expiry_days * 86400,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite=settings.refresh_cookie_samesite,
        path=settings.refresh_cookie_path,
    )


def _clear_refresh_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path=settings.refresh_cookie_path,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite=settings.refresh_cookie_samesite,
    )


def _mfa_required(detail: str = "mfa code required") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers=_MFA_HEADERS,
    )


async def _verify_second_factor(
    settings: Settings,
    users: UserRepository,
    user: UserView,
    code: str | None,
) -> int:
    """Validate a TOTP code for an MFA-enabled account and return its step."""

    state = await users.mfa_state(user.id)
    if state is None or not state.sealed_secret:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="MFA is enabled but no secret is stored; contact an administrator",
        )
    if code is None:
        raise _mfa_required()
    try:
        secret = decrypt_secret(settings, state.sealed_secret)
    except MfaConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="stored MFA secret cannot be opened; re-enrol the second factor",
        ) from exc
    verification = verify_totp(
        secret,
        code,
        interval=settings.mfa_totp_interval_seconds,
        digits=settings.mfa_totp_digits,
        window=settings.mfa_totp_window,
        last_step=state.last_step,
    )
    if not verification.valid or verification.step is None:
        await users.record_failed_mfa(user.id)
        raise _mfa_required("invalid mfa code")
    return verification.step


@router.post("/token")
async def issue_token(
    payload: TokenRequest,
    request: Request,
    response: Response,
    settings: Settings = Depends(get_app_settings),
    users: UserRepository = Depends(get_user_repository),
    refresh_tokens: RefreshTokenRepository = Depends(get_refresh_token_repository),
    audit: AuditService = Depends(get_audit_service),
) -> dict[str, object]:
    """Authenticate a user (password + optional TOTP) and issue tokens."""

    user_agent, ip_address = _client_meta(request)
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
            await _audit(
                audit,
                AuditEventType.USER_LOGIN_FAILED,
                actor=payload.username,
                entity_id=payload.username,
                payload={
                    "reason": str(exc),
                    "ip_address": ip_address,
                    "user_agent": user_agent,
                    "mfa": False,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(exc),
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc
        if user.mfa_enabled:
            step = await _verify_second_factor(settings, users, user, payload.totp_code)
            await users.record_mfa_step(user.id, step)
        principal = _principal_for_user(user)

    if user is not None:
        raw_refresh, _ = await refresh_tokens.issue(
            user.id,
            ttl_days=settings.refresh_token_expiry_days,
            user_agent=user_agent,
            ip_address=ip_address,
        )
        _set_refresh_cookie(response, settings, raw_refresh)
    await _audit(
        audit,
        AuditEventType.USER_LOGIN_SUCCEEDED,
        actor=principal.sub,
        entity_id=str(user.id) if user is not None else principal.sub,
        payload={
            "mfa": bool(user is not None and user.mfa_enabled),
            "ip_address": ip_address,
            "user_agent": user_agent,
            "bootstrap_admin": user is None,
        },
    )
    return _token_response(settings, principal)


@router.post("/refresh")
async def refresh_token(
    request: Request,
    response: Response,
    payload: RefreshTokenRequest | None = None,
    settings: Settings = Depends(get_app_settings),
    users: UserRepository = Depends(get_user_repository),
    refresh_tokens: RefreshTokenRepository = Depends(get_refresh_token_repository),
) -> dict[str, object]:
    """Rotate the HttpOnly refresh cookie and issue a fresh access token.

    A body token is still accepted for non-browser API clients, but the cookie
    takes precedence and the rotated token is only written to the cookie.
    """

    raw_refresh = request.cookies.get(settings.refresh_cookie_name)
    if not raw_refresh and payload is not None:
        raw_refresh = payload.refresh_token
    if not raw_refresh:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="refresh token is missing",
        )
    try:
        rotated_token, rotated = await refresh_tokens.rotate(
            raw_refresh,
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
    _set_refresh_cookie(response, settings, rotated_token)
    return _token_response(settings, _principal_for_user(user))


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    payload: LogoutRequest | None = None,
    settings: Settings = Depends(get_app_settings),
    principal: Principal | None = Depends(get_optional_principal),
    users: UserRepository = Depends(get_user_repository),
    refresh_tokens: RefreshTokenRepository = Depends(get_refresh_token_repository),
    blocklist: TokenBlocklistRepository = Depends(get_token_blocklist_repository),
    audit: AuditService = Depends(get_audit_service),
) -> dict[str, object]:
    """Revoke the refresh cookie/session, blocklist the JTI and clear the cookie.

    The access token is optional: an expired token must not stop the browser
    from discarding and revoking its refresh cookie.
    """

    raw_refresh = request.cookies.get(settings.refresh_cookie_name)
    if not raw_refresh and payload is not None:
        raw_refresh = payload.refresh_token
    user = await users.get_by_username(principal.sub) if principal is not None else None
    if principal is not None and principal.jti is not None:
        await blocklist.revoke(
            principal.jti,
            user_id=user.id if user is not None else None,
            expires_at=datetime.now(UTC) + timedelta(minutes=settings.jwt_expiry_minutes),
            reason="logout",
        )
    refresh_revoked = False
    if raw_refresh:
        refresh_revoked = await refresh_tokens.revoke(raw_refresh)
    _clear_refresh_cookie(response, settings)
    _, ip_address = _client_meta(request)
    actor = principal.sub if principal is not None else "anonymous"
    await _audit(
        audit,
        AuditEventType.USER_LOGOUT,
        actor=actor,
        entity_id=str(user.id) if user is not None else actor,
        payload={"refresh_revoked": refresh_revoked, "ip_address": ip_address},
    )
    return {"status": "logged_out", "refresh_revoked": refresh_revoked}


@router.post("/logout/all")
async def logout_all_devices(
    request: Request,
    settings: Settings = Depends(get_app_settings),
    principal: Principal = Depends(get_principal),
    users: UserRepository = Depends(get_user_repository),
    refresh_tokens: RefreshTokenRepository = Depends(get_refresh_token_repository),
    blocklist: TokenBlocklistRepository = Depends(get_token_blocklist_repository),
    audit: AuditService = Depends(get_audit_service),
) -> dict[str, object]:
    """Revoke every refresh session of the authenticated principal."""

    user = await users.get_by_username(principal.sub)
    revoked = 0
    if user is not None:
        revoked = await refresh_tokens.revoke_all_for_user(user.id)
    if principal.jti is not None:
        await blocklist.revoke(
            principal.jti,
            user_id=user.id if user is not None else None,
            expires_at=datetime.now(UTC) + timedelta(minutes=settings.jwt_expiry_minutes),
            reason="logout_all",
        )
    _, ip_address = _client_meta(request)
    await _audit(
        audit,
        AuditEventType.USER_LOGGED_OUT_ALL_DEVICES,
        actor=principal.sub,
        entity_id=str(user.id) if user is not None else principal.sub,
        payload={"refresh_tokens_revoked": revoked, "ip_address": ip_address},
    )
    return {"status": "logged_out_all", "refresh_tokens_revoked": revoked}


@router.get("/me")
async def me(
    users: UserRepository = Depends(get_user_repository),
    principal: Principal = Depends(require_roles(Role.ADMIN, Role.SALES, Role.APPROVER, Role.VIEWER)),
) -> dict[str, object]:
    user = await users.get_by_username(principal.sub)
    return {
        "subject": principal.sub,
        "roles": [role.value for role in principal.roles],
        "mfa_enabled": user.mfa_enabled if user is not None else None,
    }


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreatePayload,
    principal: Principal = Depends(require_roles(Role.ADMIN)),
    users: UserRepository = Depends(get_user_repository),
    audit: AuditService = Depends(get_audit_service),
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
    await _audit(
        audit,
        AuditEventType.USER_CREATED,
        actor=principal.sub,
        entity_id=str(user.id),
        payload={"username": user.username, "roles": [role.value for role in user.roles]},
    )
    return {"user": user.as_dict()}


@router.post("/password/change")
async def change_password(
    payload: PasswordChangePayload,
    principal: Principal = Depends(get_principal),
    users: UserRepository = Depends(get_user_repository),
    refresh_tokens: RefreshTokenRepository = Depends(get_refresh_token_repository),
    audit: AuditService = Depends(get_audit_service),
) -> dict[str, object]:
    """Change the caller's password and revoke every refresh session."""

    user = await users.get_by_username(principal.sub)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="the bootstrap admin password is managed by configuration",
        )
    try:
        await users.change_password(user.id, payload.current_password, payload.new_password)
    except AuthenticationError as exc:
        await _audit(
            audit,
            AuditEventType.USER_LOGIN_FAILED,
            actor=principal.sub,
            entity_id=str(user.id),
            payload={"reason": "password change rejected: current password", "mfa": False},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except UserRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    revoked = await refresh_tokens.revoke_all_for_user(user.id)
    await _audit(
        audit,
        AuditEventType.USER_PASSWORD_CHANGED,
        actor=principal.sub,
        entity_id=str(user.id),
        payload={"refresh_tokens_revoked": revoked},
    )
    return {"status": "password_changed", "refresh_tokens_revoked": revoked}


@router.post("/password/reset/request", status_code=status.HTTP_202_ACCEPTED)
async def request_password_reset(
    payload: PasswordResetRequestPayload,
    request: Request,
    settings: Settings = Depends(get_app_settings),
    users: UserRepository = Depends(get_user_repository),
    reset_tokens: PasswordResetTokenRepository = Depends(get_password_reset_repository),
    audit: AuditService = Depends(get_audit_service),
) -> dict[str, object]:
    """Issue a reset token without disclosing whether the account exists.

    The raw token is delivered out-of-band through the Telegram notifier when it
    is configured; it is never returned by the API and never logged.
    """

    _, ip_address = _client_meta(request)
    user = await users.get_by_username(payload.username)
    delivered = False
    if user is not None and user.is_active:
        raw_token, _ = await reset_tokens.issue(
            user.id,
            ttl_minutes=settings.password_reset_token_expiry_minutes,
            issued_by="self-service",
            ip_address=ip_address,
        )
        delivered = await _deliver_reset_token(settings, user.username, raw_token)
    await _audit(
        audit,
        AuditEventType.USER_PASSWORD_RESET_REQUESTED,
        actor=payload.username,
        entity_id=str(user.id) if user is not None else payload.username,
        payload={
            "account_exists": user is not None,
            "delivered": delivered,
            "ip_address": ip_address,
        },
    )
    return {"status": "reset_requested"}


async def _deliver_reset_token(settings: Settings, username: str, token: str) -> bool:
    notifier = TelegramNotifier(settings)
    if not notifier.configured:
        logger.warning("password reset token for %s was not delivered: Telegram is not configured", username)
        return False
    message = (
        f"Yasmina password reset for {username}\n"
        f"Token: {token}\n"
        f"Valid for {settings.password_reset_token_expiry_minutes} minutes."
    )
    try:
        return await notifier.notify(message)
    except Exception:  # pragma: no cover - best-effort delivery channel
        logger.exception("password reset token delivery failed")
        return False


@router.post("/password/reset")
async def reset_password(
    payload: PasswordResetPayload,
    settings: Settings = Depends(get_app_settings),
    users: UserRepository = Depends(get_user_repository),
    reset_tokens: PasswordResetTokenRepository = Depends(get_password_reset_repository),
    refresh_tokens: RefreshTokenRepository = Depends(get_refresh_token_repository),
    audit: AuditService = Depends(get_audit_service),
) -> dict[str, object]:
    """Consume a single-use reset token and replace the account password."""

    # Validate the new password before burning the token, so a weak password
    # does not force the user to request a fresh reset link.
    try:
        validate_password(payload.new_password, min_length=settings.password_min_length)
    except PasswordHashError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    try:
        user_id = await reset_tokens.consume(payload.token)
    except PasswordResetTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    try:
        user = await users.reset_password(user_id, payload.new_password)
    except UserRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    revoked = await refresh_tokens.revoke_all_for_user(user.id)
    await _audit(
        audit,
        AuditEventType.USER_PASSWORD_RESET_COMPLETED,
        actor=user.username,
        entity_id=str(user.id),
        payload={"refresh_tokens_revoked": revoked},
    )
    return {"status": "password_reset", "refresh_tokens_revoked": revoked}


@router.post("/users/{username}/password/reset", status_code=status.HTTP_201_CREATED)
async def admin_issue_password_reset(
    username: str,
    request: Request,
    settings: Settings = Depends(get_app_settings),
    principal: Principal = Depends(require_roles(Role.ADMIN)),
    users: UserRepository = Depends(get_user_repository),
    reset_tokens: PasswordResetTokenRepository = Depends(get_password_reset_repository),
    audit: AuditService = Depends(get_audit_service),
) -> dict[str, object]:
    """Admin-issued reset token for out-of-band delivery to a locked-out user."""

    user = await users.get_by_username(username)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="user account is inactive")
    _, ip_address = _client_meta(request)
    raw_token, view = await reset_tokens.issue(
        user.id,
        ttl_minutes=settings.password_reset_token_expiry_minutes,
        issued_by=principal.sub,
        ip_address=ip_address,
    )
    await _audit(
        audit,
        AuditEventType.USER_PASSWORD_RESET_REQUESTED,
        actor=principal.sub,
        entity_id=str(user.id),
        payload={"channel": "admin", "issued_for": user.username, "ip_address": ip_address},
    )
    return {
        "status": "reset_issued",
        "username": user.username,
        "reset_token": raw_token,
        "expires_in": settings.password_reset_token_expiry_minutes * 60,
        "expires_at": view.expires_at.isoformat(),
    }


@router.post("/mfa/setup")
async def setup_mfa(
    settings: Settings = Depends(get_app_settings),
    principal: Principal = Depends(get_principal),
    users: UserRepository = Depends(get_user_repository),
) -> dict[str, object]:
    """Generate a TOTP secret; MFA activates only after a code is verified."""

    user = await users.get_by_username(principal.sub)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="the bootstrap admin cannot enrol MFA",
        )
    if user.mfa_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="MFA is already enabled; disable it before enrolling again",
        )
    secret = generate_secret()
    await users.start_mfa_enrolment(user.id, encrypt_secret(settings, secret))
    return {
        "secret": secret,
        "otpauth_uri": build_otpauth_uri(
            secret,
            account=user.username,
            issuer=settings.mfa_issuer,
            interval=settings.mfa_totp_interval_seconds,
            digits=settings.mfa_totp_digits,
        ),
        "issuer": settings.mfa_issuer,
        "digits": settings.mfa_totp_digits,
        "period": settings.mfa_totp_interval_seconds,
    }


@router.post("/mfa/verify")
async def verify_mfa_enrolment(
    payload: MfaCodePayload,
    settings: Settings = Depends(get_app_settings),
    principal: Principal = Depends(get_principal),
    users: UserRepository = Depends(get_user_repository),
    audit: AuditService = Depends(get_audit_service),
) -> dict[str, object]:
    """Enable MFA once the provisioning code proves the authenticator works."""

    user = await users.get_by_username(principal.sub)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="the bootstrap admin cannot enrol MFA",
        )
    if user.mfa_enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="MFA is already enabled")
    state = await users.mfa_state(user.id)
    if state is None or not state.sealed_secret:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="MFA enrolment has not been started",
        )
    try:
        secret = decrypt_secret(settings, state.sealed_secret)
    except MfaConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    verification = verify_totp(
        secret,
        payload.code,
        interval=settings.mfa_totp_interval_seconds,
        digits=settings.mfa_totp_digits,
        window=settings.mfa_totp_window,
        last_step=state.last_step,
    )
    if not verification.valid or verification.step is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid mfa code",
        )
    await users.activate_mfa(user.id, verification.step)
    await _audit(
        audit,
        AuditEventType.USER_MFA_ENABLED,
        actor=principal.sub,
        entity_id=str(user.id),
        payload={"issuer": settings.mfa_issuer, "digits": settings.mfa_totp_digits},
    )
    return {"status": "mfa_enabled", "mfa_enabled": True}


@router.post("/mfa/disable")
async def disable_mfa(
    payload: MfaDisablePayload,
    settings: Settings = Depends(get_app_settings),
    principal: Principal = Depends(get_principal),
    users: UserRepository = Depends(get_user_repository),
    refresh_tokens: RefreshTokenRepository = Depends(get_refresh_token_repository),
    audit: AuditService = Depends(get_audit_service),
) -> dict[str, object]:
    """Disable MFA, requiring both the account password and a live TOTP code."""

    user = await users.get_by_username(principal.sub)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="the bootstrap admin cannot manage MFA",
        )
    if not user.mfa_enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="MFA is not enabled")
    try:
        await users.authenticate(user.username, payload.password)
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    await _verify_second_factor(settings, users, user, payload.code)
    await users.disable_mfa(user.id)
    revoked = await refresh_tokens.revoke_all_for_user(user.id)
    await _audit(
        audit,
        AuditEventType.USER_MFA_DISABLED,
        actor=principal.sub,
        entity_id=str(user.id),
        payload={"refresh_tokens_revoked": revoked},
    )
    return {"status": "mfa_disabled", "mfa_enabled": False}
