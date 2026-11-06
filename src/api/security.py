"""JWT authentication and role-based access control primitives."""

from __future__ import annotations

import hmac
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import jwt
from pydantic import BaseModel, Field

from src.config import Settings
from src.models.enums import Role

__all__ = [
    "AuthError",
    "Principal",
    "Role",
    "SecurityConfigurationError",
    "authenticate_admin",
    "create_access_token",
    "decode_access_token",
    "validate_security_configuration",
]


class AuthError(ValueError):
    """Raised when a token or credential is invalid."""


class SecurityConfigurationError(AuthError):
    """Raised when startup security configuration is unsafe."""


class Principal(BaseModel):
    sub: str = Field(min_length=1)
    roles: list[Role] = Field(min_length=1)
    jti: UUID | None = None
    exp: int | None = None

    def has_any(self, required: tuple[Role, ...]) -> bool:
        return Role.ADMIN in self.roles or bool(set(required) & set(self.roles))


def create_access_token(
    settings: Settings,
    subject: str,
    roles: list[Role],
    *,
    expires_minutes: int | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    minutes = expires_minutes if expires_minutes is not None else settings.jwt_expiry_minutes
    payload: dict[str, Any] = {
        "sub": subject,
        "roles": [role.value for role in roles],
        "jti": str(uuid4()),
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=minutes)).timestamp()),
        "iss": settings.jwt_issuer,
    }
    return jwt.encode(
        payload,
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


def decode_access_token(token: str, settings: Settings) -> Principal:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            options={"require": ["sub", "roles", "exp", "iat"]},
        )
    except jwt.PyJWTError as exc:
        raise AuthError(f"invalid bearer token: {exc}") from exc

    try:
        return Principal.model_validate(payload)
    except Exception as exc:
        raise AuthError("token payload has invalid claims") from exc


def authenticate_admin(username: str, password: str, settings: Settings) -> Principal:
    valid_user = hmac.compare_digest(username, settings.api_admin_username)
    valid_password = hmac.compare_digest(password, settings.api_admin_password.get_secret_value())
    if not (valid_user and valid_password):
        raise AuthError("invalid username or password")
    return Principal(sub=username, roles=[Role.ADMIN])


def validate_security_configuration(settings: Settings) -> None:
    """Fail closed when production secrets or password hashing are unsafe."""

    secret = settings.jwt_secret_key.get_secret_value()
    if not secret.strip():
        raise SecurityConfigurationError("JWT_SECRET_KEY must not be empty")
    if len(secret) < 32:
        raise SecurityConfigurationError(
            "JWT_SECRET_KEY must contain at least 32 characters"
        )
    if settings.app_env in {"staging", "production"}:
        if "change-me" in secret.lower():
            raise SecurityConfigurationError("JWT_SECRET_KEY still contains a placeholder value")
        if len(settings.api_admin_password.get_secret_value()) < 12:
            raise SecurityConfigurationError(
                "API_ADMIN_PASSWORD must contain at least 12 characters in staging/production"
            )
    try:
        import argon2  # noqa: F401
    except ImportError as exc:  # pragma: no cover - mandatory dependency
        raise SecurityConfigurationError(
            "argon2-cffi is required for password hashing"
        ) from exc
    if settings.password_hash_algorithm != "argon2id":
        raise SecurityConfigurationError("only Argon2id password hashing is supported")
