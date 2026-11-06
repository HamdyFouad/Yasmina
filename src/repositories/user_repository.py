"""User identity, password authentication and refresh-token sessions."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import delete, or_, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import Role
from src.models.orm import PasswordResetToken, RefreshToken, TokenBlocklist, User
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
    "MfaState",
    "PasswordResetTokenError",
    "PasswordResetTokenRepository",
    "PasswordResetTokenView",
    "RefreshTokenRepository",
    "RefreshTokenReuseError",
    "RefreshTokenView",
    "TokenBlocklistRepository",
    "UserRepository",
    "UserRepositoryError",
    "UserView",
    "hash_password_reset_token",
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
    mfa_enabled: bool = False
    password_changed_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "username": self.username,
            "email": self.email,
            "roles": [role.value for role in self.roles],
            "is_active": self.is_active,
            "mfa_enabled": self.mfa_enabled,
        }


@dataclass(frozen=True, slots=True)
class MfaState:
    """Sealed TOTP secret plus replay-protection state for one account."""

    user_id: UUID
    enabled: bool
    sealed_secret: str | None
    last_step: int | None


@dataclass(frozen=True, slots=True)
class PasswordResetTokenView:
    id: UUID
    user_id: UUID
    expires_at: datetime
    used_at: datetime | None

    @property
    def usable(self) -> bool:
        return self.used_at is None


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
        mfa_enabled=row.mfa_enabled,
        password_changed_at=row.password_changed_at,
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
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class UserRepository:
    """Tenant-local user directory with Argon2id password verification."""

    def __init__(self, session: AsyncSession, *, password_min_length: int = 12) -> None:
        self.session = session
        self.password_min_length = password_min_length

    async def get(self, user_id: UUID) -> UserView | None:
        row = await self.session.get(User, user_id)
        return _user_view(row) if row is not None else None

    async def get_by_username(self, username: str) -> UserView | None:
        row = (await self.session.execute(select(User).where(User.username == username))).scalars().first()
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
            password_changed_at=datetime.now(UTC),
            mfa_enabled=False,
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
        current = now or datetime.now(UTC)
        row = (await self.session.execute(select(User).where(User.username == username))).scalars().first()
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
            row.password_changed_at = datetime.now(UTC)
        except PasswordHashError as exc:
            raise UserRepositoryError(str(exc)) from exc
        await self.session.commit()
        return _user_view(row)

    async def change_password(
        self,
        user_id: UUID,
        current_password: str,
        new_password: str,
        *,
        now: datetime | None = None,
    ) -> UserView:
        """Verify the current password and replace it with a new one."""

        row = (
            (await self.session.execute(select(User).where(User.id == user_id).with_for_update()))
            .scalars()
            .first()
        )
        if row is None:
            raise UserRepositoryError(f"user {user_id} does not exist")
        if not row.is_active:
            raise UserRepositoryError("user account is inactive")
        if not verify_password(current_password, row.password_hash):
            raise AuthenticationError("current password is incorrect")
        if current_password == new_password:
            raise UserRepositoryError("the new password must differ from the current password")
        try:
            validate_password(new_password, min_length=self.password_min_length)
            row.password_hash = hash_password(new_password, min_length=self.password_min_length)
        except PasswordHashError as exc:
            raise UserRepositoryError(str(exc)) from exc
        row.password_changed_at = now or datetime.now(UTC)
        row.failed_login_attempts = 0
        row.locked_until = None
        await self.session.commit()
        return _user_view(row)

    async def reset_password(
        self,
        user_id: UUID,
        new_password: str,
        *,
        now: datetime | None = None,
    ) -> UserView:
        """Set a password without knowing the previous one (reset-token flow)."""

        row = (
            (await self.session.execute(select(User).where(User.id == user_id).with_for_update()))
            .scalars()
            .first()
        )
        if row is None:
            raise UserRepositoryError(f"user {user_id} does not exist")
        if not row.is_active:
            raise UserRepositoryError("user account is inactive")
        try:
            validate_password(new_password, min_length=self.password_min_length)
            row.password_hash = hash_password(new_password, min_length=self.password_min_length)
        except PasswordHashError as exc:
            raise UserRepositoryError(str(exc)) from exc
        row.password_changed_at = now or datetime.now(UTC)
        row.failed_login_attempts = 0
        row.locked_until = None
        await self.session.commit()
        return _user_view(row)

    async def mfa_state(self, user_id: UUID) -> MfaState | None:
        row = await self.session.get(User, user_id)
        if row is None:
            return None
        return MfaState(
            user_id=row.id,
            enabled=row.mfa_enabled,
            sealed_secret=row.mfa_secret,
            last_step=row.mfa_last_step,
        )

    async def start_mfa_enrolment(self, user_id: UUID, sealed_secret: str) -> None:
        """Store a sealed secret; MFA stays disabled until a code is verified."""

        row = await self.session.get(User, user_id)
        if row is None:
            raise UserRepositoryError(f"user {user_id} does not exist")
        row.mfa_secret = sealed_secret
        row.mfa_enabled = False
        row.mfa_enrolled_at = None
        row.mfa_last_step = None
        await self.session.commit()

    async def activate_mfa(self, user_id: UUID, step: int, *, now: datetime | None = None) -> None:
        row = (
            (await self.session.execute(select(User).where(User.id == user_id).with_for_update()))
            .scalars()
            .first()
        )
        if row is None:
            raise UserRepositoryError(f"user {user_id} does not exist")
        if not row.mfa_secret:
            raise UserRepositoryError("MFA enrolment has not been started")
        row.mfa_enabled = True
        row.mfa_enrolled_at = now or datetime.now(UTC)
        row.mfa_last_step = step
        await self.session.commit()

    async def disable_mfa(self, user_id: UUID) -> None:
        row = await self.session.get(User, user_id)
        if row is None:
            raise UserRepositoryError(f"user {user_id} does not exist")
        row.mfa_enabled = False
        row.mfa_secret = None
        row.mfa_enrolled_at = None
        row.mfa_last_step = None
        await self.session.commit()

    async def record_mfa_step(self, user_id: UUID, step: int) -> None:
        """Persist the accepted TOTP step so the same code cannot be replayed."""

        row = await self.session.get(User, user_id)
        if row is None:
            raise UserRepositoryError(f"user {user_id} does not exist")
        row.mfa_last_step = step
        await self.session.commit()

    async def record_failed_mfa(self, user_id: UUID, *, now: datetime | None = None) -> None:
        """Count a bad TOTP code towards the shared login lockout."""

        current = now or datetime.now(UTC)
        row = (
            (await self.session.execute(select(User).where(User.id == user_id).with_for_update()))
            .scalars()
            .first()
        )
        if row is None:
            raise UserRepositoryError(f"user {user_id} does not exist")
        row.failed_login_attempts += 1
        if row.failed_login_attempts >= _MAX_FAILED_ATTEMPTS:
            row.locked_until = current + timedelta(minutes=_LOCKOUT_MINUTES)
            row.failed_login_attempts = 0
        await self.session.commit()


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
        current = now or datetime.now(UTC)
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
        current = now or datetime.now(UTC)
        row = (
            (
                await self.session.execute(
                    select(RefreshToken)
                    .where(RefreshToken.token_hash == hash_refresh_token(raw_token))
                    .with_for_update()
                )
            )
            .scalars()
            .first()
        )
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
        current = now or datetime.now(UTC)
        row = (
            (
                await self.session.execute(
                    select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(raw_token))
                )
            )
            .scalars()
            .first()
        )
        if row is None or row.revoked_at is not None:
            return False
        row.revoked_at = current
        await self.session.commit()
        return True

    async def revoke_family(self, family_id: UUID, *, now: datetime | None = None) -> int:
        count = await self._revoke_family(family_id, now or datetime.now(UTC))
        await self.session.commit()
        return count

    async def revoke_all_for_user(self, user_id: UUID, *, now: datetime | None = None) -> int:
        """Revoke every live refresh session of a user ("logout everywhere")."""

        current = now or datetime.now(UTC)
        rows = (
            (
                await self.session.execute(
                    select(RefreshToken).where(
                        RefreshToken.user_id == user_id,
                        RefreshToken.revoked_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            row.revoked_at = current
        await self.session.commit()
        return len(rows)

    async def purge_expired(
        self,
        *,
        now: datetime | None = None,
        revoked_retention_days: int = 7,
    ) -> int:
        """Delete expired sessions and revoked sessions past the retention window."""

        current = now or datetime.now(UTC)
        cutoff = current - timedelta(days=max(0, revoked_retention_days))
        result = await self.session.execute(
            delete(RefreshToken).where(
                or_(
                    RefreshToken.expires_at < current,
                    (RefreshToken.revoked_at.is_not(None)) & (RefreshToken.revoked_at < cutoff),
                )
            )
        )
        await self.session.commit()
        return int(cast(CursorResult[Any], result).rowcount or 0)

    async def _revoke_family(self, family_id: UUID, now: datetime) -> int:
        rows = (
            (
                await self.session.execute(
                    select(RefreshToken).where(
                        RefreshToken.family_id == family_id,
                        RefreshToken.revoked_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
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
        current = now or datetime.now(UTC)
        result = await self.session.execute(delete(TokenBlocklist).where(TokenBlocklist.expires_at < current))
        await self.session.commit()
        return int(cast(CursorResult[Any], result).rowcount or 0)


def hash_password_reset_token(token: str) -> str:
    """Hash a single-use password reset token before persistence."""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class PasswordResetTokenError(ValueError):
    """Raised when a reset token is unknown, used or expired."""


class PasswordResetTokenRepository:
    """Single-use, hashed, short-lived password reset tokens."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def issue(
        self,
        user_id: UUID,
        *,
        ttl_minutes: int = 15,
        issued_by: str | None = None,
        ip_address: str | None = None,
        now: datetime | None = None,
    ) -> tuple[str, PasswordResetTokenView]:
        current = now or datetime.now(UTC)
        # Only the newest token for an account stays usable.
        await self.invalidate_for_user(user_id, now=current)
        raw = secrets.token_urlsafe(32)
        row = PasswordResetToken(
            id=uuid4(),
            user_id=user_id,
            token_hash=hash_password_reset_token(raw),
            expires_at=current + timedelta(minutes=max(1, ttl_minutes)),
            issued_by=(issued_by or "")[:128] or None,
            requested_ip=(ip_address or "")[:64] or None,
        )
        self.session.add(row)
        await self.session.commit()
        return raw, PasswordResetTokenView(
            id=row.id,
            user_id=row.user_id,
            expires_at=row.expires_at,
            used_at=row.used_at,
        )

    async def consume(self, raw_token: str, *, now: datetime | None = None) -> UUID:
        """Validate and burn a reset token, returning the owning user id."""

        current = now or datetime.now(UTC)
        row = (
            (
                await self.session.execute(
                    select(PasswordResetToken)
                    .where(PasswordResetToken.token_hash == hash_password_reset_token(raw_token))
                    .with_for_update()
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            raise PasswordResetTokenError("invalid password reset token")
        if row.used_at is not None:
            raise PasswordResetTokenError("password reset token has already been used")
        if _datetime(row.expires_at) <= current:
            row.used_at = current
            await self.session.commit()
            raise PasswordResetTokenError("password reset token has expired")
        row.used_at = current
        await self.session.commit()
        return row.user_id

    async def invalidate_for_user(self, user_id: UUID, *, now: datetime | None = None) -> int:
        current = now or datetime.now(UTC)
        rows = (
            (
                await self.session.execute(
                    select(PasswordResetToken).where(
                        PasswordResetToken.user_id == user_id,
                        PasswordResetToken.used_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            row.used_at = current
        return len(rows)

    async def purge_expired(self, *, now: datetime | None = None) -> int:
        current = now or datetime.now(UTC)
        result = await self.session.execute(
            delete(PasswordResetToken).where(
                or_(
                    PasswordResetToken.expires_at < current,
                    PasswordResetToken.used_at.is_not(None),
                )
            )
        )
        await self.session.commit()
        return int(cast(CursorResult[Any], result).rowcount or 0)
