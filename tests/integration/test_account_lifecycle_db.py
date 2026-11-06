"""Live PostgreSQL tests for P4.8 account lifecycle, MFA and token retention."""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from src.config import Settings
from src.db import dispose_engine, get_session_factory
from src.main import create_app
from src.models.enums import AuditEventType, Role
from src.models.orm import (
    AuditLog,
    PasswordResetToken,
    RefreshToken,
    TokenBlocklist,
    User,
)
from src.repositories.user_repository import (
    PasswordResetTokenRepository,
    RefreshTokenRepository,
    UserRepository,
    hash_refresh_token,
)
from src.services.mfa_service import totp_at
from src.services.token_maintenance import purge_auth_tokens

DATABASE_URL = os.getenv("YASMINA_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="set YASMINA_TEST_DATABASE_URL to run PostgreSQL integration tests",
)

PREFIX = "p48-"
PASSWORD = "A-Strong-Password-42!"


def settings() -> Settings:
    assert DATABASE_URL is not None
    return Settings(
        _env_file=None,
        database_url=DATABASE_URL,
        app_env="test",
        jwt_secret_key="live-test-jwt-secret-with-at-least-32-bytes",
        api_admin_password="test-admin-password",
        app_secret_key="live-test-app-secret-for-mfa",
        rate_limit_auth_per_minute=200,
        password_reset_token_expiry_minutes=15,
    )


def _username() -> str:
    return f"{PREFIX}{uuid4().hex[:12]}"


async def _cleanup(session) -> None:  # type: ignore[no-untyped-def]
    user_ids = (
        (await session.execute(select(User.id).where(User.username.like(f"{PREFIX}%")))).scalars().all()
    )
    for user_id in user_ids:
        await session.execute(delete(RefreshToken).where(RefreshToken.user_id == user_id))
        await session.execute(delete(TokenBlocklist).where(TokenBlocklist.user_id == user_id))
        await session.execute(delete(PasswordResetToken).where(PasswordResetToken.user_id == user_id))
        await session.execute(delete(User).where(User.id == user_id))
    await session.commit()


async def _create_user(factory, username: str, password: str = PASSWORD):  # type: ignore[no-untyped-def]
    async with factory() as session:
        return await UserRepository(session).create(username, password, [Role.SALES])


def _client(current_settings: Settings) -> TestClient:
    return TestClient(create_app(current_settings, run_startup_checks=False))


def _login(client: TestClient, username: str, password: str = PASSWORD, **extra: str):  # type: ignore[no-untyped-def]
    payload: dict[str, str] = {"username": username, "password": password}
    payload.update(extra)
    return client.post("/api/v1/auth/token", json=payload)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _audit_events(session, **filters: str) -> list[AuditLog]:  # type: ignore[no-untyped-def]
    statement = select(AuditLog).where(
        *[getattr(AuditLog, column) == value for column, value in filters.items()]
    )
    return list((await session.execute(statement.order_by(AuditLog.id))).scalars().all())


async def test_password_change_revokes_sessions_and_is_audited() -> None:
    current_settings = settings()
    factory = get_session_factory(current_settings)
    username = _username()
    new_password = "A-New-Strong-Password-43!"
    try:
        async with factory() as session:
            await _cleanup(session)
            user = await _create_user(factory, username)
        await dispose_engine()
        with _client(current_settings) as client:
            login = _login(client, username)
            assert login.status_code == 200, login.text
            access = login.json()["access_token"]
            refresh = login.json()["refresh_token"]

            change = client.post(
                "/api/v1/auth/password/change",
                json={"current_password": PASSWORD, "new_password": new_password},
                headers=_bearer(access),
            )
            assert change.status_code == 200, change.text
            assert change.json()["refresh_tokens_revoked"] == 1

            assert _login(client, username).status_code == 401
            assert _login(client, username, new_password).status_code == 200
            assert client.post("/api/v1/auth/refresh", json={"refresh_token": refresh}).status_code == 401

            wrong_current = client.post(
                "/api/v1/auth/password/change",
                json={"current_password": "wrong-password", "new_password": "Another-Password-44!"},
                headers=_bearer(access),
            )
            assert wrong_current.status_code == 400

        async with factory() as session:
            changed = await _audit_events(
                session,
                event_type=AuditEventType.USER_PASSWORD_CHANGED.value,
                entity_id=str(user.id),
            )
            assert len(changed) == 1
            failed = await _audit_events(
                session,
                event_type=AuditEventType.USER_LOGIN_FAILED.value,
                actor=username,
            )
            assert len(failed) >= 2  # old password + rejected handler call
            succeeded = await _audit_events(
                session,
                event_type=AuditEventType.USER_LOGIN_SUCCEEDED.value,
                entity_id=str(user.id),
            )
            assert len(succeeded) == 2
    finally:
        async with get_session_factory(current_settings)() as session:
            await _cleanup(session)
        await dispose_engine()


async def test_admin_issued_reset_token_is_single_use() -> None:
    current_settings = settings()
    factory = get_session_factory(current_settings)
    username = _username()
    new_password = "A-Reset-Password-45!"
    try:
        async with factory() as session:
            await _cleanup(session)
            user = await _create_user(factory, username)
        await dispose_engine()
        with _client(current_settings) as client:
            admin_login = client.post(
                "/api/v1/auth/token",
                json={
                    "username": current_settings.api_admin_username,
                    "password": current_settings.api_admin_password.get_secret_value(),
                },
            )
            assert admin_login.status_code == 200, admin_login.text
            admin_access = admin_login.json()["access_token"]
            assert "refresh_token" not in admin_login.json()

            issued = client.post(
                f"/api/v1/auth/users/{username}/password/reset",
                headers=_bearer(admin_access),
            )
            assert issued.status_code == 201, issued.text
            reset_token = issued.json()["reset_token"]
            assert issued.json()["expires_in"] > 0

            reset = client.post(
                "/api/v1/auth/password/reset",
                json={"token": reset_token, "new_password": new_password},
            )
            assert reset.status_code == 200, reset.text
            assert reset.json()["status"] == "password_reset"

            reused = client.post(
                "/api/v1/auth/password/reset",
                json={"token": reset_token, "new_password": "Another-Password-46!"},
            )
            assert reused.status_code == 400
            assert "already been used" in reused.json()["detail"]

            short = client.post(
                "/api/v1/auth/password/reset",
                json={"token": reset_token, "new_password": "short"},
            )
            assert short.status_code == 422

            assert _login(client, username, new_password).status_code == 200

            anonymous = client.post(
                "/api/v1/auth/password/reset/request",
                json={"username": username},
            )
            assert anonymous.status_code == 202
            assert "reset_token" not in anonymous.json()

        async with factory() as session:
            completed = await _audit_events(
                session,
                event_type=AuditEventType.USER_PASSWORD_RESET_COMPLETED.value,
                entity_id=str(user.id),
            )
            assert len(completed) == 1
            requested = await _audit_events(
                session,
                event_type=AuditEventType.USER_PASSWORD_RESET_REQUESTED.value,
                entity_id=str(user.id),
            )
            # admin-issued token request plus the self-service request
            assert len(requested) == 2
    finally:
        async with get_session_factory(current_settings)() as session:
            await _cleanup(session)
        await dispose_engine()


async def test_mfa_enrolment_login_challenge_and_replay_protection() -> None:
    current_settings = settings()
    factory = get_session_factory(current_settings)
    username = _username()
    interval = current_settings.mfa_totp_interval_seconds
    try:
        async with factory() as session:
            await _cleanup(session)
            user = await _create_user(factory, username)
        await dispose_engine()
        with _client(current_settings) as client:
            login = _login(client, username)
            assert login.status_code == 200, login.text
            access = login.json()["access_token"]

            setup = client.post("/api/v1/auth/mfa/setup", headers=_bearer(access))
            assert setup.status_code == 200, setup.text
            secret = setup.json()["secret"]
            assert setup.json()["otpauth_uri"].startswith("otpauth://totp/")

            enrol_code = totp_at(secret, interval=interval)
            verified = client.post(
                "/api/v1/auth/mfa/verify",
                json={"code": enrol_code},
                headers=_bearer(access),
            )
            assert verified.status_code == 200, verified.text
            assert verified.json()["mfa_enabled"] is True

            challenged = _login(client, username)
            assert challenged.status_code == 401
            assert challenged.headers["X-Yasmina-MFA-Required"] == "true"
            assert challenged.json()["detail"] == "mfa code required"

            wrong = _login(client, username, totp_code="000000")
            assert wrong.status_code == 401
            assert wrong.json()["detail"] == "invalid mfa code"

            next_code = totp_at(secret, timestamp=time.time() + interval, interval=interval)
            mfa_login = _login(client, username, totp_code=next_code)
            assert mfa_login.status_code == 200, mfa_login.text
            mfa_access = mfa_login.json()["access_token"]

            replay = _login(client, username, totp_code=next_code)
            assert replay.status_code == 401

            me = client.get("/api/v1/auth/me", headers=_bearer(mfa_access))
            assert me.status_code == 200
            assert me.json()["mfa_enabled"] is True

        async with factory() as session:
            enabled = await _audit_events(
                session,
                event_type=AuditEventType.USER_MFA_ENABLED.value,
                entity_id=str(user.id),
            )
            assert len(enabled) == 1
    finally:
        async with get_session_factory(current_settings)() as session:
            await _cleanup(session)
        await dispose_engine()


async def test_mfa_can_be_disabled_with_password_and_live_code() -> None:
    current_settings = settings()
    factory = get_session_factory(current_settings)
    username = _username()
    interval = current_settings.mfa_totp_interval_seconds
    try:
        async with factory() as session:
            await _cleanup(session)
            user = await _create_user(factory, username)
        await dispose_engine()
        with _client(current_settings) as client:
            access = _login(client, username).json()["access_token"]
            secret = client.post("/api/v1/auth/mfa/setup", headers=_bearer(access)).json()["secret"]
            assert (
                client.post(
                    "/api/v1/auth/mfa/verify",
                    json={"code": totp_at(secret, interval=interval)},
                    headers=_bearer(access),
                ).status_code
                == 200
            )

            wrong_password = client.post(
                "/api/v1/auth/mfa/disable",
                json={"password": "wrong-password", "code": totp_at(secret, interval=interval)},
                headers=_bearer(access),
            )
            assert wrong_password.status_code == 400

            disable_code = totp_at(secret, timestamp=time.time() + interval, interval=interval)
            disabled = client.post(
                "/api/v1/auth/mfa/disable",
                json={"password": PASSWORD, "code": disable_code},
                headers=_bearer(access),
            )
            assert disabled.status_code == 200, disabled.text
            assert _login(client, username).status_code == 200

        async with factory() as session:
            audit = await _audit_events(
                session,
                event_type=AuditEventType.USER_MFA_DISABLED.value,
                entity_id=str(user.id),
            )
            assert len(audit) == 1
    finally:
        async with get_session_factory(current_settings)() as session:
            await _cleanup(session)
        await dispose_engine()


async def test_logout_all_devices_revokes_every_session() -> None:
    current_settings = settings()
    factory = get_session_factory(current_settings)
    username = _username()
    try:
        async with factory() as session:
            await _cleanup(session)
            user = await _create_user(factory, username)
        await dispose_engine()
        with _client(current_settings) as client:
            first = _login(client, username)
            second = _login(client, username)
            assert first.status_code == 200 and second.status_code == 200
            first_access = first.json()["access_token"]
            first_refresh = first.json()["refresh_token"]
            second_refresh = second.json()["refresh_token"]

            logout_all = client.post("/api/v1/auth/logout/all", headers=_bearer(first_access))
            assert logout_all.status_code == 200, logout_all.text
            assert logout_all.json()["refresh_tokens_revoked"] == 2

            for token in (first_refresh, second_refresh):
                response = client.post("/api/v1/auth/refresh", json={"refresh_token": token})
                assert response.status_code == 401
            blocked = client.get("/api/v1/auth/me", headers=_bearer(first_access))
            assert blocked.status_code == 401

        async with factory() as session:
            audit = await _audit_events(
                session,
                event_type=AuditEventType.USER_LOGGED_OUT_ALL_DEVICES.value,
                entity_id=str(user.id),
            )
            assert len(audit) == 1
    finally:
        async with get_session_factory(current_settings)() as session:
            await _cleanup(session)
        await dispose_engine()


async def test_token_cleanup_purges_only_expired_and_stale_rows() -> None:
    current_settings = settings()
    factory = get_session_factory(current_settings)
    username = _username()
    now = datetime.now(UTC)
    try:
        async with factory() as session:
            await _cleanup(session)
            user = await _create_user(factory, username)
            repository = RefreshTokenRepository(session)
            live_token, _ = await repository.issue(user.id, ttl_days=30)
            expired_token, _ = await repository.issue(user.id, ttl_days=30)
            expired_row = (
                await session.execute(
                    select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(expired_token))
                )
            ).scalar_one()
            expired_row.expires_at = now - timedelta(days=1)
            stale_revoked, _ = await repository.issue(user.id, ttl_days=30)
            stale_row = (
                await session.execute(
                    select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(stale_revoked))
                )
            ).scalar_one()
            stale_row.revoked_at = now - timedelta(days=30)
            await session.commit()

            session.add_all(
                [
                    TokenBlocklist(
                        jti=uuid4(),
                        user_id=user.id,
                        expires_at=now - timedelta(minutes=1),
                        reason="logout",
                    ),
                    TokenBlocklist(
                        jti=uuid4(),
                        user_id=user.id,
                        expires_at=now + timedelta(minutes=15),
                        reason="logout",
                    ),
                ]
            )
            live_jti = uuid4()
            session.add(
                TokenBlocklist(
                    jti=live_jti,
                    user_id=user.id,
                    expires_at=now + timedelta(minutes=15),
                    reason="logout",
                )
            )
            reset_repository = PasswordResetTokenRepository(session)
            used_token, _ = await reset_repository.issue(user.id, ttl_minutes=15)
            await reset_repository.consume(used_token)
            await session.commit()

            dry_run = await purge_auth_tokens(session, now=now, retention_days=7, dry_run=True)
            assert dry_run.dry_run is True
            assert dry_run.refresh_tokens >= 2
            assert dry_run.blocklisted_jtis >= 1
            assert dry_run.password_reset_tokens >= 1
            still_there = await session.get(
                RefreshToken,
                (
                    await session.execute(
                        select(RefreshToken.id).where(
                            RefreshToken.token_hash == hash_refresh_token(expired_token)
                        )
                    )
                ).scalar_one(),
            )
            assert still_there is not None

            result = await purge_auth_tokens(session, now=now, retention_days=7)
            assert result.refresh_tokens >= 2
            assert result.blocklisted_jtis >= 1
            assert result.password_reset_tokens >= 1

            remaining_hashes = set(
                (
                    await session.execute(
                        select(RefreshToken.token_hash).where(RefreshToken.user_id == user.id)
                    )
                )
                .scalars()
                .all()
            )
            assert hash_refresh_token(live_token) in remaining_hashes
            assert hash_refresh_token(expired_token) not in remaining_hashes
            assert hash_refresh_token(stale_revoked) not in remaining_hashes

            live_block = await session.get(TokenBlocklist, live_jti)
            assert live_block is not None
            reset_rows = (
                (
                    await session.execute(
                        select(PasswordResetToken).where(PasswordResetToken.user_id == user.id)
                    )
                )
                .scalars()
                .all()
            )
            assert reset_rows == []

            cleanup_audit = await _audit_events(
                session,
                event_type=AuditEventType.SYSTEM_TOKEN_CLEANUP.value,
            )
            assert cleanup_audit
    finally:
        async with get_session_factory(current_settings)() as session:
            await _cleanup(session)
        await dispose_engine()
