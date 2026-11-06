"""Password hashing, security configuration and token-bucket tests."""

from __future__ import annotations

import pytest

from src.api.rate_limit import TokenBucketRateLimiter
from src.api.security import SecurityConfigurationError, validate_security_configuration
from src.config import Settings
from src.services.password_service import (
    PasswordHashError,
    hash_password,
    password_needs_rehash,
    validate_password,
    verify_password,
)


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "database_url": None,
        "jwt_secret_key": "test-jwt-secret-key-with-at-least-32-bytes",
        "api_admin_password": "test-admin-password",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_argon2_password_hash_roundtrip() -> None:
    raw = "Correct-Horse-Battery-42!"
    hashed = hash_password(raw)

    assert hashed.startswith("$argon2id$")
    assert hashed != raw
    assert verify_password(raw, hashed) is True
    assert verify_password("wrong-password", hashed) is False
    assert password_needs_rehash(hashed) is False


def test_password_policy_rejects_short_passwords() -> None:
    with pytest.raises(PasswordHashError, match="at least 12"):
        validate_password("short")
    with pytest.raises(PasswordHashError, match="letters"):
        validate_password("123456789012")


def test_security_configuration_rejects_short_production_jwt() -> None:
    settings = make_settings(jwt_secret_key="short-secret")

    with pytest.raises(SecurityConfigurationError, match="32 characters"):
        validate_security_configuration(settings)


def test_security_configuration_accepts_test_settings() -> None:
    validate_security_configuration(make_settings(app_env="test"))


def test_token_bucket_denies_then_refills() -> None:
    now = [100.0]
    limiter = TokenBucketRateLimiter(clock=lambda: now[0])

    first = limiter.check("auth:127.0.0.1", limit=2, window_seconds=60)
    second = limiter.check("auth:127.0.0.1", limit=2, window_seconds=60)
    denied = limiter.check("auth:127.0.0.1", limit=2, window_seconds=60)

    assert first.allowed is True
    assert second.allowed is True
    assert denied.allowed is False
    assert denied.retry_after > 0

    now[0] += 31
    assert limiter.check("auth:127.0.0.1", limit=2, window_seconds=60).allowed is True
