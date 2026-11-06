"""Retention job for expired authentication tokens.

Deleting expired rows keeps ``refresh_tokens``, ``token_blocklist`` and
``password_reset_tokens`` bounded without weakening security: a refresh token is
only removable after its own expiry (plus a configurable retention window for
revoked sessions), and a blocklisted JTI only after the access token it guards
has expired.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from src.models.enums import AuditEventType
from src.models.orm import PasswordResetToken, RefreshToken, TokenBlocklist
from src.repositories.user_repository import (
    PasswordResetTokenRepository,
    RefreshTokenRepository,
    TokenBlocklistRepository,
)
from src.services.audit_service import AuditService

__all__ = ["TokenCleanupResult", "purge_auth_tokens"]


@dataclass(frozen=True, slots=True)
class TokenCleanupResult:
    refresh_tokens: int = 0
    blocklisted_jtis: int = 0
    password_reset_tokens: int = 0
    retention_days: int = 7
    dry_run: bool = False

    @property
    def total(self) -> int:
        return self.refresh_tokens + self.blocklisted_jtis + self.password_reset_tokens

    def as_dict(self) -> dict[str, Any]:
        return {
            "refresh_tokens": self.refresh_tokens,
            "blocklisted_jtis": self.blocklisted_jtis,
            "password_reset_tokens": self.password_reset_tokens,
            "total": self.total,
            "retention_days": self.retention_days,
            "dry_run": self.dry_run,
        }


def _refresh_predicate(current: datetime, cutoff: datetime) -> ColumnElement[bool]:
    return or_(
        RefreshToken.expires_at < current,
        (RefreshToken.revoked_at.is_not(None)) & (RefreshToken.revoked_at < cutoff),
    )


async def purge_auth_tokens(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    retention_days: int = 7,
    dry_run: bool = False,
    actor: str = "yasmina-admin",
) -> TokenCleanupResult:
    """Purge expired refresh tokens, blocklist entries and reset tokens."""

    current = now or datetime.now(UTC)
    retention = max(0, retention_days)
    cutoff = current - timedelta(days=retention)

    if dry_run:
        refresh_count = int(
            (
                await session.execute(
                    select(func.count()).select_from(RefreshToken).where(_refresh_predicate(current, cutoff))
                )
            ).scalar_one()
        )
        blocklist_count = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(TokenBlocklist)
                    .where(TokenBlocklist.expires_at < current)
                )
            ).scalar_one()
        )
        reset_count = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(PasswordResetToken)
                    .where(
                        or_(
                            PasswordResetToken.expires_at < current,
                            PasswordResetToken.used_at.is_not(None),
                        )
                    )
                )
            ).scalar_one()
        )
        return TokenCleanupResult(
            refresh_tokens=refresh_count,
            blocklisted_jtis=blocklist_count,
            password_reset_tokens=reset_count,
            retention_days=retention,
            dry_run=True,
        )

    refresh_deleted = await RefreshTokenRepository(session).purge_expired(
        now=current,
        revoked_retention_days=retention,
    )
    blocklist_deleted = await TokenBlocklistRepository(session).purge_expired(now=current)
    reset_deleted = await PasswordResetTokenRepository(session).purge_expired(now=current)
    result = TokenCleanupResult(
        refresh_tokens=refresh_deleted,
        blocklisted_jtis=blocklist_deleted,
        password_reset_tokens=reset_deleted,
        retention_days=retention,
    )
    await AuditService(session).append(
        event_type=AuditEventType.SYSTEM_TOKEN_CLEANUP,
        actor=actor,
        entity_type="auth_tokens",
        entity_id="retention",
        payload=result.as_dict(),
    )
    return result
