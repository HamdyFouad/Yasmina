"""Worker health endpoint and snapshot aggregation tests."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from src.api.deps import get_worker_health_service
from src.config import Settings
from src.main import create_app
from src.services.worker_metrics import WorkerHealthService


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url=None,
        jwt_secret_key="test-worker-health-jwt-secret-with-32-bytes",
        api_admin_password="test-admin-password",
    )


def make_app(service: object) -> TestClient:
    app = create_app(make_settings(), run_startup_checks=False)
    app.dependency_overrides[get_worker_health_service] = lambda: service
    return TestClient(app)


class FakeService:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    async def snapshot(self) -> dict[str, object]:
        if self.fail:
            raise RuntimeError("database unavailable")
        return {
            "status": "ok",
            "runs": {"latest": {}, "counts": {"succeeded": 2}},
            "outbox": {"pending": 0, "failed": 0},
            "recent": [],
        }


def test_worker_health_endpoint_returns_snapshot() -> None:
    with make_app(FakeService()) as client:
        response = client.get("/healthz/worker")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["runs"]["counts"]["succeeded"] == 2


def test_worker_health_endpoint_returns_503_when_metrics_unavailable() -> None:
    with make_app(FakeService(fail=True)) as client:
        response = client.get("/healthz/worker")

    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"


class FakeRuns:
    def __init__(self) -> None:
        self.run = SimpleNamespace(
            status="succeeded",
            as_dict=lambda: {
                "run_type": "scrape",
                "status": "succeeded",
                "duration_ms": 1200,
                "items_processed": 4,
                "errors_count": 0,
            },
        )

    async def latest_by_type(self) -> dict[str, object]:
        return {"scrape": self.run}

    async def status_counts(self) -> dict[str, int]:
        return {"running": 0, "succeeded": 1, "failed": 0, "skipped": 0, "cancelled": 0}

    async def recent(self, *, limit: int = 20) -> list[object]:
        return [self.run]


class FakeJobs:
    def __init__(self, failed: int = 0) -> None:
        self.failed = failed

    async def status_counts(self) -> dict[str, int]:
        return {"pending": 1, "processing": 0, "completed": 2, "skipped": 0, "failed": self.failed}


async def test_worker_health_service_marks_dead_letter_queue_degraded() -> None:
    service = WorkerHealthService(FakeRuns(), FakeJobs(failed=1))  # type: ignore[arg-type]

    snapshot = await service.snapshot()

    assert snapshot["status"] == "degraded"
    assert snapshot["runs"]["latest"]["scrape"]["duration_ms"] == 1200
    assert snapshot["outbox"]["failed"] == 1
