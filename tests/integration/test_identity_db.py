"""Live PostgreSQL tests for user identity, refresh rotation and revocation."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from src.config import Settings
from src.db import dispose_engine, get_session_factory
from src.main import create_app
from src.models.enums import Role
from src.models.orm import RefreshToken, TokenBlocklist, User
from src.repositories.user_repository import (
    AuthenticationError,
    RefreshTokenRepository,
    RefreshTokenReuseError,
    TokenBlocklistRepository,
    UserRepository,
)

DATABASE_URL = os.getenv("YASMINA_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="set YASMINA_TEST_DATABASE_URL to run PostgreSQL integration tests",
)


def settings() -> Settings:
    assert DATABASE_URL is not None
    return Settings(
        _env_file=None,
        database_url=DATABASE_URL,
        app_env="test",
        jwt_secret_key="live-test-jwt-secret-with-at-least-32-bytes",
        api_admin_password="test-admin-password",
    )


async def _cleanup(session) -> None:  # type: ignore[no-untyped-def]
    user_ids = (await session.execute(select(User.id).where(User.username.like("p45-%")))).scalars().all()
    for user_id in user_ids:
        await session.execute(delete(RefreshToken).where(RefreshToken.user_id == user_id))
        await session.execute(delete(TokenBlocklist).where(TokenBlocklist.user_id == user_id))
        await session.execute(delete(User).where(User.id == user_id))
    await session.commit()


async def test_user_password_and_rotation_family_reuse_detection() -> None:
    factory = get_session_factory(settings())
    username = f"p45-{uuid4().hex[:10]}"
    try:
        async with factory() as session:
            await _cleanup(session)
            users = UserRepository(session)
            user = await users.create(
                username,
                "A-Strong-Password-42!",
                [Role.SALES, Role.APPROVER],
                email=f"{username}@example.com",
            )
            assert user.roles == (Role.SALES, Role.APPROVER)

            with pytest.raises(AuthenticationError, match="invalid username or password"):
                await users.authenticate(username, "wrong-password")

            authenticated = await users.authenticate(username, "A-Strong-Password-42!")
            assert authenticated.id == user.id

            refresh_tokens = RefreshTokenRepository(session)
            first_token, first = await refresh_tokens.issue(user.id, ttl_days=30)
            second_token, second = await refresh_tokens.rotate(first_token)
            assert second.family_id == first.family_id
            assert second_token != first_token

            with pytest.raises(RefreshTokenReuseError, match="reuse"):
                await refresh_tokens.rotate(first_token)

            with pytest.raises(AuthenticationError, match="revoked"):
                await refresh_tokens.rotate(second_token)
    finally:
        async with factory() as session:
            await _cleanup(session)
        await dispose_engine()


async def test_refresh_token_revocation_and_access_jti_blocklist() -> None:
    factory = get_session_factory(settings())
    username = f"p45-{uuid4().hex[:10]}"
    try:
        async with factory() as session:
            await _cleanup(session)
            user = await UserRepository(session).create(
                username,
                "A-Strong-Password-42!",
                [Role.VIEWER],
            )
            refresh_tokens = RefreshTokenRepository(session)
            token, _ = await refresh_tokens.issue(user.id, ttl_days=30)
            assert await refresh_tokens.revoke(token) is True
            with pytest.raises(AuthenticationError, match="revoked"):
                await refresh_tokens.rotate(token)

            blocklist = TokenBlocklistRepository(session)
            jti = uuid4()
            await blocklist.revoke(
                jti,
                user_id=user.id,
                expires_at=datetime.now(UTC) + timedelta(minutes=15),
                reason="logout",
            )
            assert await blocklist.is_revoked(jti) is True
            assert await blocklist.is_revoked(uuid4()) is False
    finally:
        async with factory() as session:
            await _cleanup(session)
        await dispose_engine()


async def test_auth_api_login_refresh_reuse_and_logout_blocklist() -> None:
    current_settings = settings()
    factory = get_session_factory(current_settings)
    username = f"p45-{uuid4().hex[:10]}"
    password = "A-Strong-Password-42!"
    try:
        async with factory() as session:
            await _cleanup(session)
            await UserRepository(session).create(username, password, [Role.SALES])

        # TestClient runs its own event loop; dispose the pytest-loop engine
        # before handing the URL to the application lifespan.
        await dispose_engine()
        app = create_app(current_settings, run_startup_checks=False)
        with TestClient(app) as client:
            login = client.post(
                "/api/v1/auth/token",
                json={"username": username, "password": password},
            )
            assert login.status_code == 200, login.text
            first_access = login.json()["access_token"]
            first_refresh = login.json()["refresh_token"]
            assert login.json()["expires_in"] <= 900

            me = client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {first_access}"},
            )
            assert me.status_code == 200

            refreshed = client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": first_refresh},
            )
            assert refreshed.status_code == 200, refreshed.text
            second_access = refreshed.json()["access_token"]
            second_refresh = refreshed.json()["refresh_token"]

            reused = client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": first_refresh},
            )
            assert reused.status_code == 401
            assert "reuse" in reused.json()["detail"]

            family_revoked = client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": second_refresh},
            )
            assert family_revoked.status_code == 401

            logout = client.post(
                "/api/v1/auth/logout",
                json={"refresh_token": second_refresh},
                headers={"Authorization": f"Bearer {second_access}"},
            )
            assert logout.status_code == 200, logout.text

            blocked = client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {second_access}"},
            )
            assert blocked.status_code == 401
            assert "revoked" in blocked.json()["detail"]
    finally:
        cleanup_factory = get_session_factory(current_settings)
        async with cleanup_factory() as session:
            await _cleanup(session)
        await dispose_engine()
