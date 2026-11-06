"""User identity, password authentication and refresh-token sessions."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import Role
from src.models.orm import RefreshToken, TokenBlocklist, User
from src.services.password_service import (
    PasswordHashError,
    dummy_verify,
    hash_password,
    password_needs_rehash,
    validate_password,
    verify_password,
)

__all__ = [
    "AuthenticationError",
    "IssuedRefreshToken",
    "RefreshTokenRepository",
    "RefreshTokenReuseError",
    "RefreshTokenView",
    "TokenBlocklistRepository",
    "UserRepository",
    "UserRepositoryError",
    "UserView",
    "hash_refresh_token",
]

_MAX_FAILED_ATTEMPTS = 5
_LOCKOUT_MINUTES = 15


class UserRepositoryError(ValueError):
    """Raised when a user cannot be created or updated."""


class AuthenticationError(ValueError):
    """Raised when credentials are invalid, inactive or locked."""


class RefreshTokenReuseError(AuthenticationError):
    """Raised when a rotated refresh token is presented again."""


@dataclass(frozen=True, slots=True)
class UserView:
    id: UUID
    username: str
    email: str | None
    roles: tuple[Role, ...]
    is_active: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "username": self.username,
            "email": self.email,
            "roles": [role.value for role in self.roles],
            "is_active": self.is_active,
        }


@dataclass(frozen=True, slots=True)
class RefreshTokenView:
    id: UUID
    user_id: UUID
    family_id: UUID
    expires_at: datetime
    revoked_at: datetime | None
    replaced_by_id: UUID | None

    @property
    def active(self) -> bool:
        return self.revoked_at is None


@dataclass(frozen=True, slots=True)
class IssuedRefreshToken:
    token: str
    view: RefreshTokenView
    user: UserView


def _user_view(row: User) -> UserView:
    roles: list[Role] = []
    for value in row.roles or []:
        try:
            roles.append(Role(str(value)))
        except ValueError:  # pragma: no cover - database drift guard
            continue
    return UserView(
        id=row.id,
        username=row.username,
        email=row.email,
        roles=tuple(roles),
        is_active=row.is_active,
    )


def _refresh_view(row: RefreshToken) -> RefreshTokenView:
    return RefreshTokenView(
        id=row.id,
        user_id=row.user_id,
        family_id=row.family_id,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
        replaced_by_id=row.replaced_by_id,
    )


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _datetime(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class UserRepository:
    """Tenant-local user directory with Argon2id password verification."""

    def __init__(self, session: AsyncSession, *, password_min_length: int = 12) -> None:
        self.session = session
        self.password_min_length = password_min_length

    async def get(self, user_id: UUID) -> UserView | None:
        row = await self.session.get(User, user_id)
        return _user_view(row) if row is not None else None

    async def get_by_username(self, username: str) -> UserView | None:
        row = (
            await self.session.execute(select(User).where(User.username == username))
        ).scalars().first()
        return _user_view(row) if row is not None else None

    async def create(
        self,
        username: str,
        password: str,
        roles: list[Role] | tuple[Role, ...],
        *,
        email: str | None = None,
    ) -> UserView:
        username = username.strip()
        if not username or len(username) > 128:
            raise UserRepositoryError("username must contain 1-128 characters")
        normalized_roles = tuple(dict.fromkeys(Role(role) for role in roles))
        if not normalized_roles:
            raise UserRepositoryError("a user must have at least one role")
        try:
            password_hash = hash_password(password, min_length=self.password_min_length)
        except PasswordHashError as exc:
            raise UserRepositoryError(str(exc)) from exc

        row = User(
            username=username,
            email=email.strip().lower() if email else None,
            password_hash=password_hash,
            roles=[role.value for role in normalized_roles],
            is_active=True,
        )
        self.session.add(row)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise UserRepositoryError(f"username or email already exists: {exc}") from exc
        return _user_view(row)

    async def authenticate(
        self,
        username: str,
        password: str,
        *,
        now: datetime | None = None,
    ) -> UserView:
        current = now or datetime.now(timezone.utc)
        row = (
            await self.session.execute(select(User).where(User.username == username))
        ).scalars().first()
        if row is None:
            dummy_verify(password)
            raise AuthenticationError("invalid username or password")
        if not row.is_active:
            dummy_verify(password)
            raise AuthenticationError("user account is inactive")
        if row.locked_until is not None and _datetime(row.locked_until) > current:
            dummy_verify(password)
            raise AuthenticationError("user account is temporarily locked")

        if not verify_password(password, row.password_hash):
            row.failed_login_attempts += 1
            if row.failed_login_attempts >= _MAX_FAILED_ATTEMPTS:
                row.locked_until = current + timedelta(minutes=_LOCKOUT_MINUTES)
                row.failed_login_attempts = 0
            await self.session.commit()
            raise AuthenticationError("invalid username or password")

        row.failed_login_attempts = 0
        row.locked_until = None
        row.last_login_at = current
        if password_needs_rehash(row.password_hash):
            row.password_hash = hash_password(password, min_length=self.password_min_length)
        await self.session.commit()
        return _user_view(row)

    async def set_password(self, user_id: UUID, password: str) -> UserView:
        row = await self.session.get(User, user_id)
        if row is None:
            raise UserRepositoryError(f"user {user_id} does not exist")
        try:
            validate_password(password, min_length=self.password_min_length)
            row.password_hash = hash_password(password, min_length=self.password_min_length)
        except PasswordHashError as exc:
            raise UserRepositoryError(str(exc)) from exc
        await self.session.commit()
        return _user_view(row)


class RefreshTokenRepository:
    """Durable, rotating refresh-token families with reuse detection."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def issue(
        self,
        user_id: UUID,
        *,
        ttl_days: int = 30,
        family_id: UUID | None = None,
        user_agent: str | None = None,
        ip_address: str | None = None,
        now: datetime | None = None,
    ) -> tuple[str, RefreshTokenView]:
        current = now or datetime.now(timezone.utc)
        raw, row = self._new_row(
            user_id,
            family_id=family_id or uuid4(),
            ttl_days=ttl_days,
            user_agent=user_agent,
            ip_address=ip_address,
            now=current,
        )
        self.session.add(row)
        await self.session.commit()
        return raw, _refresh_view(row)

    async def rotate(
        self,
        raw_token: str,
        *,
        ttl_days: int = 30,
        user_agent: str | None = None,
        ip_address: str | None = None,
        now: datetime | None = None,
    ) -> tuple[str, RefreshTokenView]:
        current = now or datetime.now(timezone.utc)
        row = (
            await self.session.execute(
                select(RefreshToken)
                .where(RefreshToken.token_hash == hash_refresh_token(raw_token))
                .with_for_update()
            )
        ).scalars().first()
        if row is None:
            raise AuthenticationError("invalid refresh token")
        if row.revoked_at is not None:
            if row.replaced_by_id is not None:
                await self._revoke_family(row.family_id, current)
                await self.session.commit()
                raise RefreshTokenReuseError("refresh token reuse detected")
            raise AuthenticationError("refresh token has been revoked")
        if _datetime(row.expires_at) <= current:
            row.revoked_at = current
            await self.session.commit()
            raise AuthenticationError("refresh token has expired")

        raw, replacement = self._new_row(
            row.user_id,
            family_id=row.family_id,
            ttl_days=ttl_days,
            user_agent=user_agent,
            ip_address=ip_address,
            now=current,
        )
        self.session.add(replacement)
        row.revoked_at = current
        row.replaced_by_id = replacement.id
        await self.session.commit()
        return raw, _refresh_view(replacement)

    async def revoke(
        self,
        raw_token: str,
        *,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        current = now or datetime.now(timezone.utc)
        row = (
            await self.session.execute(
                select(RefreshToken).where(
                    RefreshToken.token_hash == hash_refresh_token(raw_token)
                )
            )
        ).scalars().first()
        if row is None or row.revoked_at is not None:
            return False
        row.revoked_at = current
        await self.session.commit()
        return True

    async def revoke_family(self, family_id: UUID, *, now: datetime | None = None) -> int:
        count = await self._revoke_family(family_id, now or datetime.now(timezone.utc))
        await self.session.commit()
        return count

    async def _revoke_family(self, family_id: UUID, now: datetime) -> int:
        rows = (
            await self.session.execute(
                select(RefreshToken).where(
                    RefreshToken.family_id == family_id,
                    RefreshToken.revoked_at.is_(None),
                )
            )
        ).scalars().all()
        for row in rows:
            row.revoked_at = now
        return len(rows)

    def _new_row(
        self,
        user_id: UUID,
        *,
        family_id: UUID,
        ttl_days: int,
        user_agent: str | None,
        ip_address: str | None,
        now: datetime,
    ) -> tuple[str, RefreshToken]:
        raw = secrets.token_urlsafe(48)
        row = RefreshToken(
            id=uuid4(),
            user_id=user_id,
            family_id=family_id,
            token_hash=hash_refresh_token(raw),
            expires_at=now + timedelta(days=ttl_days),
            user_agent=(user_agent or "")[:256] or None,
            ip_address=(ip_address or "")[:64] or None,
        )
        return raw, row


class TokenBlocklistRepository:
    """Persisted revoked access-token JTIs."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def revoke(
        self,
        jti: UUID,
        *,
        user_id: UUID | None = None,
        expires_at: datetime,
        reason: str | None = None,
    ) -> None:
        existing = await self.session.get(TokenBlocklist, jti)
        if existing is not None:
            return
        self.session.add(
            TokenBlocklist(
                jti=jti,
                user_id=user_id,
                expires_at=expires_at,
                reason=(reason or "")[:64] or None,
            )
        )
        await self.session.commit()

    async def is_revoked(self, jti: UUID | None) -> bool:
        if jti is None:
            return False
        return await self.session.get(TokenBlocklist, jti) is not None

    async def purge_expired(self, *, now: datetime | None = None) -> int:
        current = now or datetime.now(timezone.utc)
        rows = (
            await self.session.execute(
                select(TokenBlocklist).where(TokenBlocklist.expires_at < current)
            )
        ).scalars().all()
        for row in rows:
            await self.session.delete(row)
        if rows:
            await self.session.commit()
        return len(rows)
