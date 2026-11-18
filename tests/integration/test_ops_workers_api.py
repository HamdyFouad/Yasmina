"""Operations worker/ingestion console endpoints (mocked repositories)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from src.api.deps import (
    get_audit_service,
    get_ingestion_job_repository,
    get_session,
    get_worker_command_launcher,
    get_worker_run_repository,
)
from src.api.routes import ops as ops_routes
from src.api.security import Role, create_access_token
from src.config import Settings
from src.main import create_app
from src.repositories.ingestion_repository import (
    IngestionJobView,
    IngestionRepositoryError,
)
from src.repositories.worker_run_repository import WorkerRunView
from src.scrapers.portal_config import PortalTarget
from src.scrapers.probe import ProbePortal, ProbeReport
from src.services.worker_command import WorkerCommandResult

SETTINGS = Settings(
    _env_file=None,
    database_url=None,
    app_env="test",
    jwt_secret_key="ops-workers-jwt-secret-with-32-bytes",
    api_admin_password="test-admin-password",
)
ADMIN = {"Authorization": f"Bearer {create_access_token(SETTINGS, 'admin', [Role.ADMIN])}"}
SALES = {"Authorization": f"Bearer {create_access_token(SETTINGS, 'sales', [Role.SALES])}"}


class FakeSession:
    async def get(self, model: Any, key: Any) -> None:
        return None


class FakeAudit:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    async def append(self, **kwargs: Any) -> None:
        self.entries.append(kwargs)


def make_run(status: str = "succeeded") -> WorkerRunView:
    return WorkerRunView(
        id=uuid4(),
        run_type="scrape",
        resource="bayern",
        status=status,
        worker_id="worker-1",
        started_at=datetime(2026, 2, 1, 10, 0, tzinfo=UTC),
        finished_at=datetime(2026, 2, 1, 10, 0, 5, tzinfo=UTC),
        duration_ms=5000,
        items_processed=3,
        errors_count=0 if status == "succeeded" else 1,
        error=None if status == "succeeded" else "selector changed",
        details={"pages_fetched": 2},
    )


class FakeWorkerRuns:
    def __init__(self) -> None:
        self.runs = [make_run(), make_run("failed")]

    async def list_runs(self, **kwargs: Any) -> list[WorkerRunView]:
        return self.runs

    async def count_runs(self, **kwargs: Any) -> int:
        return len(self.runs)


def make_job(
    *,
    status: str = "failed",
    last_error: str | None = "SECURITY: Eicar-Test-Signature",
    job_id: UUID | None = None,
) -> IngestionJobView:
    return IngestionJobView(
        id=job_id or uuid4(),
        tender_id=uuid4(),
        portal_name="bayern",
        status=status,
        attempts=5,
        max_attempts=5,
        attachments=({"filename": "notice.pdf"},),
        raw_object="raw/notice.pdf",
        available_at=datetime(2026, 2, 1, 10, 0, tzinfo=UTC),
        last_error=last_error,
        created_at=datetime(2026, 2, 1, 9, 0, tzinfo=UTC),
        updated_at=datetime(2026, 2, 1, 10, 0, tzinfo=UTC),
        tender_number="E2E-DLQ",
        tender_title="E2E dead letter",
        quarantined=bool(last_error and last_error.startswith("SECURITY:")),
        quarantine_reason=(
            last_error.removeprefix("SECURITY: ")
            if last_error and last_error.startswith("SECURITY:")
            else None
        ),
    )


class FakeIngestion:
    def __init__(self) -> None:
        self.jobs = [make_job(), make_job(last_error="parser failed")]
        self.retried: list[UUID] = []
        self.discarded: list[tuple[UUID, str]] = []

    async def list_jobs(self, **kwargs: Any) -> list[IngestionJobView]:
        return self.jobs

    async def count_jobs(self, **kwargs: Any) -> int:
        return len(self.jobs)

    async def status_counts(self) -> dict[str, int]:
        return {"pending": 1, "processing": 0, "completed": 4, "failed": 2, "skipped": 0}

    async def quarantine_count(self) -> int:
        return 1

    async def retry_failed(self, job_id: UUID) -> IngestionJobView:
        job = next((item for item in self.jobs if item.id == job_id), None)
        if job is None:
            raise IngestionRepositoryError("ingestion job does not exist")
        if job.status != "failed":
            raise IngestionRepositoryError("only failed ingestion jobs can be retried")
        self.retried.append(job_id)
        return make_job(status="pending", last_error=None, job_id=job.id)

    async def discard(self, job_id: UUID, reason: str) -> IngestionJobView:
        job = next((item for item in self.jobs if item.id == job_id), None)
        if job is None:
            raise IngestionRepositoryError("ingestion job does not exist")
        if job.status != "failed":
            raise IngestionRepositoryError("only failed ingestion jobs can be discarded")
        self.discarded.append((job_id, reason))
        return make_job(status="skipped", last_error=reason, job_id=job.id)

    async def release_stale(self, *, older_than_seconds: int) -> int:
        return 2


class FakeLauncher:
    def launch_scrape(self, *, portal: str, max_pages: int | None) -> WorkerCommandResult:
        return WorkerCommandResult(
            action="scrape",
            command=("python", "-m", "src.worker", "scrape", "--portal", portal),
            pid=4242,
        )

    def launch_ingestion(self, *, limit: int) -> WorkerCommandResult:
        return WorkerCommandResult(
            action="ingestion",
            command=("python", "-m", "src.worker", "process", "--limit", str(limit)),
            pid=4343,
        )


def make_client() -> tuple[TestClient, FakeIngestion, FakeAudit]:
    app = create_app(SETTINGS, run_startup_checks=False)
    ingestion = FakeIngestion()
    audit = FakeAudit()
    app.dependency_overrides[get_session] = lambda: FakeSession()
    app.dependency_overrides[get_worker_run_repository] = lambda: FakeWorkerRuns()
    app.dependency_overrides[get_ingestion_job_repository] = lambda: ingestion
    app.dependency_overrides[get_worker_command_launcher] = lambda: FakeLauncher()
    app.dependency_overrides[get_audit_service] = lambda: audit
    return TestClient(app), ingestion, audit


def test_worker_runs_and_ingestion_are_admin_only() -> None:
    client, _, _ = make_client()
    with client:
        runs = client.get("/api/v1/ops/worker-runs", headers=ADMIN)
        ingestion = client.get("/api/v1/ops/ingestion", headers=ADMIN)
        forbidden = client.get("/api/v1/ops/worker-runs", headers=SALES)

    assert runs.status_code == 200, runs.text
    assert runs.json()["count"] == 2
    assert runs.json()["runs"][0]["resource"] == "bayern"
    assert ingestion.status_code == 200, ingestion.text
    assert ingestion.json()["status_counts"]["completed"] == 4
    assert ingestion.json()["quarantine_count"] == 1
    assert ingestion.json()["jobs"][0]["quarantined"] is True
    assert ingestion.json()["jobs"][0]["quarantine_reason"] == "Eicar-Test-Signature"
    assert forbidden.status_code == 403


def test_ingestion_retry_and_discard_audit_the_operator_action() -> None:
    client, ingestion, audit = make_client()
    retry_job = make_job()
    discard_job = make_job(last_error="parser failed")
    ingestion.jobs = [retry_job, discard_job]

    with client:
        retried = client.post(
            f"/api/v1/ops/ingestion/{retry_job.id}/retry",
            headers=ADMIN,
        )
        discarded = client.post(
            f"/api/v1/ops/ingestion/{discard_job.id}/discard",
            json={"reason": "superseded notice"},
            headers=ADMIN,
        )

    assert retried.status_code == 200, retried.text
    assert retried.json()["job"]["status"] == "pending"
    assert discarded.status_code == 200, discarded.text
    assert discarded.json()["job"]["status"] == "skipped"
    assert ingestion.retried == [retry_job.id]
    assert ingestion.discarded == [(discard_job.id, "superseded notice")]
    assert [entry["event_type"].value for entry in audit.entries] == [
        "SYSTEM_INGESTION_RETRIED",
        "SYSTEM_INGESTION_DISCARDED",
    ]


def test_probe_route_returns_read_only_report(monkeypatch: Any) -> None:
    client, _, audit = make_client()
    target = PortalTarget(
        id="bayern",
        name="Vergabe Bayern",
        start_url="https://example.test/",
        engine="standard",
        portal_name="bayern",
        portal_code="BY",
        country="DE",
        enabled=True,
        metadata={},
    )
    report = ProbeReport(
        portal=ProbePortal(
            id="bayern",
            name="Vergabe Bayern",
            engine="standard",
            start_url="https://example.test/",
            enabled=True,
            api_feed=False,
        ),
        max_pages=1,
        pages_fetched=1,
        notices_discovered=2,
        notices_parsed=2,
        skipped_cpv=0,
        skipped_invalid=0,
        skipped_stale=0,
        details_fetched=1,
        errors=(),
        issues=(),
        issues_truncated=0,
        warnings=(),
        elapsed_seconds=0.5,
        probed_at=datetime(2026, 2, 1, 10, 0, tzinfo=UTC),
    )
    monkeypatch.setattr(ops_routes, "load_portal_targets", lambda *args, **kwargs: [target])

    async def fake_probe(*args: Any, **kwargs: Any) -> ProbeReport:
        return report

    monkeypatch.setattr(ops_routes, "probe_portal", fake_probe)

    with client:
        response = client.post("/api/v1/ops/portals/bayern/probe", headers=ADMIN)

    assert response.status_code == 200, response.text
    assert response.json()["read_only"] is True
    assert response.json()["notices_parsed"] == 2
    assert audit.entries[-1]["event_type"].value == "SYSTEM_PORTAL_PROBED"


def test_worker_runs_compatibility_alias_is_admin_only() -> None:
    client, _, _ = make_client()
    with client:
        allowed = client.get("/healthz/worker_runs", headers=ADMIN)
        forbidden = client.get("/healthz/worker_runs", headers=SALES)

    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["count"] == 2
    assert allowed.json()["runs"][0]["resource"] == "bayern"
    assert forbidden.status_code == 403


def test_manual_worker_triggers_and_stale_lock_release_are_audited() -> None:
    client, _, audit = make_client()
    with client:
        scrape = client.post(
            "/api/v1/ops/worker-runs/scrape",
            json={"portal": "bayern", "max_pages": 2},
            headers=ADMIN,
        )
        ingestion = client.post(
            "/api/v1/ops/worker-runs/ingestion",
            json={"limit": 5},
            headers=ADMIN,
        )
        release = client.post(
            "/api/v1/ops/ingestion/release-stale",
            json={"older_than_seconds": 120},
            headers=ADMIN,
        )

    assert scrape.status_code == 200, scrape.text
    assert scrape.json()["accepted"] is True
    assert scrape.json()["pid"] == 4242
    assert ingestion.status_code == 200, ingestion.text
    assert ingestion.json()["action"] == "ingestion"
    assert release.status_code == 200, release.text
    assert release.json()["released"] == 2
    assert [entry["event_type"].value for entry in audit.entries] == [
        "SYSTEM_WORKER_SCRAPE_TRIGGERED",
        "SYSTEM_INGESTION_TRIGGERED",
        "SYSTEM_INGESTION_LOCKS_RELEASED",
    ]
