"""Prometheus metrics middleware and exposition."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.api.metrics import (
    MetricsMiddleware,
    observe_workflow,
    render_metrics,
    reset_metrics,
)
from src.api.observability import RequestContextMiddleware
from src.config import Settings


def build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(MetricsMiddleware, enabled=True)
    app.add_middleware(RequestContextMiddleware)

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/items/{item_id}")
    async def read_item(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    @app.get("/boom")
    async def boom() -> None:
        raise HTTPException(status_code=503, detail="unavailable")

    return app


def metrics_text() -> str:
    payload, content_type = render_metrics()
    assert content_type.startswith("text/plain")
    return payload.decode("utf-8")


def sample_value(text: str, metric: str, labels: str = "") -> float:
    prefix = f"{metric}{labels} "
    for line in text.splitlines():
        if line.startswith(prefix):
            return float(line[len(prefix) :])
    raise AssertionError(f"{metric}{labels} missing from exposition")


def test_http_metrics_use_route_templates_and_status_labels() -> None:
    reset_metrics()
    with TestClient(build_app()) as client:
        assert client.get("/ping").status_code == 200
        assert client.get("/items/42").status_code == 200
        assert client.get("/boom").status_code == 503
        assert client.get("/missing").status_code == 404

    text = metrics_text()

    assert sample_value(text, "yasmina_http_requests_total", '{method="GET",route="/ping",status="200"}') == 1
    assert (
        sample_value(
            text,
            "yasmina_http_requests_total",
            '{method="GET",route="/items/{item_id}",status="200"}',
        )
        == 1
    )
    assert 'route="/items/42"' not in text
    assert sample_value(text, "yasmina_http_errors_total", '{method="GET",route="/boom",status="503"}') == 1
    assert (
        sample_value(
            text,
            "yasmina_http_requests_total",
            '{method="GET",route="unmatched",status="404"}',
        )
        == 1
    )
    assert "yasmina_http_request_duration_seconds_bucket" in text


def test_request_context_sets_and_honours_request_id() -> None:
    reset_metrics()
    with TestClient(build_app()) as client:
        generated = client.get("/ping")
        assert generated.headers["x-request-id"]
        forwarded = client.get("/ping", headers={"X-Request-ID": "trace-123"})
        assert forwarded.headers["x-request-id"] == "trace-123"


def test_workflow_metrics_record_status_and_duration() -> None:
    reset_metrics()

    observe_workflow("pending_approval", 0.42)
    observe_workflow("error", 1.5)
    observe_workflow("unexpected-status", 0.1)

    text = metrics_text()

    assert sample_value(text, "yasmina_workflow_runs_total", '{status="pending_approval"}') == 1
    assert sample_value(text, "yasmina_workflow_runs_total", '{status="error"}') == 1
    assert sample_value(text, "yasmina_workflow_runs_total", '{status="other"}') == 1
    assert sample_value(text, "yasmina_workflow_duration_seconds_count", '{status="error"}') == 1
    assert sample_value(text, "yasmina_workflow_errors_total") == 1


def test_reset_metrics_clears_state() -> None:
    reset_metrics()
    with TestClient(build_app()) as client:
        client.get("/ping")
    assert 'route="/ping"' in metrics_text()

    reset_metrics()

    assert 'route="/ping"' not in metrics_text()


def _app_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "database_url": None,
        "jwt_secret_key": "test-jwt-secret-key-with-at-least-32-bytes",
        "api_admin_password": "test-admin-password",
        "rate_limit_enabled": False,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_application_exposes_prometheus_endpoint() -> None:
    from src.main import create_app

    reset_metrics()
    app = create_app(_app_settings(), run_startup_checks=False)
    with TestClient(app) as client:
        response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "yasmina_http_requests_total" in response.text
    assert "yasmina_workflow_duration_seconds" in response.text


def test_metrics_endpoint_can_be_disabled() -> None:
    from src.main import create_app

    app = create_app(
        _app_settings(metrics_enabled=False),
        run_startup_checks=False,
    )
    with TestClient(app) as client:
        response = client.get("/metrics")

    assert response.status_code == 404
