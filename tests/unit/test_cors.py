"""CORS configuration for the browser dashboard."""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.config import Settings
from src.main import create_app


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "database_url": None,
        "app_env": "test",
        "jwt_secret_key": "test-cors-jwt-secret-with-at-least-32-bytes",
        "api_admin_password": "test-admin-password",
        "rate_limit_enabled": False,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_cors_is_disabled_without_an_allow_list() -> None:
    app = create_app(make_settings(), run_startup_checks=False)

    with TestClient(app) as client:
        response = client.get("/healthz", headers={"Origin": "http://localhost:5173"})

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_cors_preflight_allows_a_configured_origin() -> None:
    app = create_app(
        make_settings(cors_allowed_origins="http://localhost:5173,https://ops.example.com"),
        run_startup_checks=False,
    )

    with TestClient(app) as client:
        response = client.options(
            "/api/v1/auth/token",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "authorization" in response.headers["access-control-allow-headers"].lower()


def test_cors_rejects_an_unknown_origin() -> None:
    app = create_app(
        make_settings(cors_allowed_origins="https://ops.example.com"),
        run_startup_checks=False,
    )

    with TestClient(app) as client:
        response = client.get("/healthz", headers={"Origin": "http://evil.example"})

    assert "access-control-allow-origin" not in response.headers


def test_configured_origins_are_normalized() -> None:
    settings = make_settings(
        cors_allowed_origins="http://localhost:5173/, https://ops.example.com ",
    )

    assert settings.cors_origins == ["http://localhost:5173", "https://ops.example.com"]
