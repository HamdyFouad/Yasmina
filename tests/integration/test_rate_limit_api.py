"""API perimeter rate-limit integration test."""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.config import Settings
from src.main import create_app


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "database_url": None,
        "app_env": "test",
        "jwt_secret_key": "test-rate-limit-jwt-secret-with-32-bytes",
        "api_admin_password": "test-admin-password",
        "rate_limit_auth_per_minute": 2,
        "rate_limit_upload_per_minute": 2,
        "rate_limit_business_per_minute": 300,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_auth_endpoint_returns_429_after_bucket_exhaustion() -> None:
    app = create_app(make_settings(), run_startup_checks=False)
    payload = {"username": "admin", "password": "test-admin-password"}

    with TestClient(app) as client:
        first = client.post("/api/v1/auth/token", json=payload)
        second = client.post("/api/v1/auth/token", json=payload)
        third = client.post("/api/v1/auth/token", json=payload)
        health = client.get("/healthz")

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert third.headers["Retry-After"]
    assert third.headers["X-RateLimit-Remaining"] == "0"
    assert health.status_code == 200


def test_buckets_follow_x_forwarded_for_when_proxies_are_trusted() -> None:
    app = create_app(
        make_settings(
            rate_limit_auth_per_minute=1,
            trust_proxy_headers=True,
        ),
        run_startup_checks=False,
    )
    payload = {"username": "admin", "password": "test-admin-password"}

    with TestClient(app) as client:
        first = client.post(
            "/api/v1/auth/token",
            json=payload,
            headers={"X-Forwarded-For": "203.0.113.10, 10.0.0.2"},
        )
        second = client.post(
            "/api/v1/auth/token",
            json=payload,
            headers={"X-Forwarded-For": "203.0.113.10, 10.0.0.2"},
        )
        other_client = client.post(
            "/api/v1/auth/token",
            json=payload,
            headers={"X-Forwarded-For": "198.51.100.20"},
        )

    assert first.status_code == 200
    assert second.status_code == 429
    assert other_client.status_code == 200


def test_forwarded_for_is_ignored_without_trust_flag() -> None:
    app = create_app(make_settings(rate_limit_auth_per_minute=1), run_startup_checks=False)
    payload = {"username": "admin", "password": "test-admin-password"}

    with TestClient(app) as client:
        first = client.post(
            "/api/v1/auth/token",
            json=payload,
            headers={"X-Forwarded-For": "203.0.113.10"},
        )
        second = client.post(
            "/api/v1/auth/token",
            json=payload,
            headers={"X-Forwarded-For": "198.51.100.20"},
        )

    assert first.status_code == 200
    assert second.status_code == 429
