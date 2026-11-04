"""JWT authentication and role-based access control primitives."""

from __future__ import annotations

import hmac
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any

import jwt
from pydantic import BaseModel, Field

from src.config import Settings

__all__ = [
    "AuthError",
    "Principal",
    "Role",
    "authenticate_admin",
    "create_access_token",
    "decode_access_token",
]


class Role(StrEnum):
    ADMIN = "admin"
    SALES = "sales"
    APPROVER = "approver"
    VIEWER = "viewer"


class AuthError(ValueError):
    """Raised when a token or credential is invalid."""


class Principal(BaseModel):
    sub: str = Field(min_length=1)
    roles: list[Role] = Field(min_length=1)

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
