"""FastAPI lifespan tests (preflight is mocked)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.config import Settings
from src.main import create_app
from src.preflight import CheckResult


def make_settings() -> Settings:
    return Settings(_env_file=None, database_url=None)


def test_health_and_readiness_endpoints(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def fake_preflight(settings, options):  # type: ignore[no-untyped-def]
        return [
            CheckResult("postgres", "ok", "PostgreSQL test"),
            CheckResult("minio", "ok", "authenticated"),
        ]

    monkeypatch.setattr("src.main.run_preflight", fake_preflight)
    app = create_app(make_settings())

    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        readiness = client.get("/readyz")
        assert readiness.status_code == 200
        assert readiness.json()["status"] == "ready"
        assert len(readiness.json()["checks"]) == 2


def test_startup_fails_when_preflight_fails(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def fake_preflight(settings, options):  # type: ignore[no-untyped-def]
        return [CheckResult("postgres", "fail", "connection refused")]

    monkeypatch.setattr("src.main.run_preflight", fake_preflight)
    app = create_app(make_settings())

    with pytest.raises(RuntimeError, match="Startup preflight failed"), TestClient(app):
        pass
