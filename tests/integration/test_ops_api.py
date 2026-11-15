"""Operations dashboard endpoint tests (activity feed, KPI and outbox)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from src.api.deps import (
    get_audit_service,
    get_ops_repository,
    get_quotation_sync_repository,
    get_session,
)
from src.api.security import Role, create_access_token
from src.config import Settings
from src.main import create_app
from src.models.enums import AuditEventType
from src.repositories.ops_repository import ActivityEvent
from src.repositories.quotation_sync_repository import (
    QuotationSyncJobView,
    QuotationSyncRepositoryError,
)


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url=None,
        app_env="test",
        jwt_secret_key="ops-api-jwt-secret-with-at-least-32-bytes",
        api_admin_password="test-admin-password",
    )


class FakeSession:
    async def get(self, model: Any, key: Any) -> None:
        return None


class FakeOpsRepository:
    def __init__(self) -> None:
        self.activity_limits: list[int] = []
        self.summary_owners: list[str | None] = []
        self.events = [
            ActivityEvent(
                id=7,
                occurred_at=datetime(2026, 2, 1, 12, 0, tzinfo=UTC),
                event_type="USER_LOGIN_SUCCEEDED",
                actor="admin",
                entity_type="user",
                entity_id="admin",
                payload={"source": "test"},
            )
        ]

    async def recent_activity(self, *, limit: int = 50) -> list[ActivityEvent]:
        self.activity_limits.append(limit)
        return self.events[:limit]

    async def summary(self, *, owner: str | None = None) -> dict[str, Any]:
        self.summary_owners.append(owner)
        return {
            "tenders": 3,
            "offers": 2,
            "pending_approvals": 1,
            "invoices": 4,
            "overdue_invoices": 1,
            "gross_invoiced": "2380.00",
            "gross_paid": "1190.00",
            "currency": "EUR",
        }


def make_client() -> tuple[TestClient, FakeOpsRepository, Settings]:
    settings = make_settings()
    repository = FakeOpsRepository()
    app = create_app(settings, run_startup_checks=False)
    app.dependency_overrides[get_session] = lambda: FakeSession()
    app.dependency_overrides[get_ops_repository] = lambda: repository
    app.dependency_overrides[get_quotation_sync_repository] = lambda: FakeQuotationSyncRepository()
    app.dependency_overrides[get_audit_service] = lambda: FakeAudit()
    return TestClient(app), repository, settings


def make_job(
    *,
    status: str = "failed",
    offer_number: str = "AN-2026-0100",
    last_error: str | None = "ERPNext unavailable",
    attempts: int = 6,
    job_id: UUID | None = None,
) -> QuotationSyncJobView:
    return QuotationSyncJobView(
        id=job_id or uuid4(),
        offer_id=uuid4(),
        offer_number=offer_number,
        status=status,
        attempts=attempts,
        max_attempts=6,
        available_at=datetime(2026, 2, 1, 12, 0, tzinfo=UTC),
        quotation_name=None,
        last_error=last_error,
        created_at=datetime(2026, 2, 1, 11, 0, tzinfo=UTC),
        updated_at=datetime(2026, 2, 1, 12, 0, tzinfo=UTC),
    )


class FakeQuotationSyncRepository:
    def __init__(self) -> None:
        self.failed = make_job()
        self.pending = make_job(
            status="pending",
            offer_number="AN-2026-0101",
            last_error=None,
            attempts=0,
        )
        self.jobs = [self.failed, self.pending]
        self.list_calls: list[dict[str, Any]] = []
        self.retried: list[UUID] = []

    async def status_counts(self) -> dict[str, int]:
        return {
            "pending": 2,
            "processing": 1,
            "succeeded": 9,
            "failed": 0,
            "skipped": 1,
        }

    async def get(self, job_id: UUID) -> QuotationSyncJobView | None:
        return next((job for job in self.jobs if job.id == job_id), None)

    async def list_jobs(
        self,
        *,
        status: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> tuple[list[QuotationSyncJobView], int]:
        self.list_calls.append({"status": status, "limit": limit, "offset": offset})
        matches = [job for job in self.jobs if status is None or job.status == status]
        return matches[offset : offset + limit], len(matches)

    async def retry_failed(self, job_id: UUID, *, now: Any | None = None) -> QuotationSyncJobView:
        job = await self.get(job_id)
        if job is None:
            raise QuotationSyncRepositoryError(f"quotation sync job {job_id} does not exist")
        if job.status != "failed":
            raise QuotationSyncRepositoryError(
                f"quotation sync job {job_id} is {job.status}; only failed jobs can be retried"
            )
        self.retried.append(job_id)
        retried = make_job(
            status="pending",
            offer_number=job.offer_number,
            last_error=None,
            attempts=0,
            job_id=job.id,
        )
        self.jobs = [retried if item.id == job.id else item for item in self.jobs]
        return retried


class FakeAudit:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    async def append(self, **kwargs: Any) -> None:
        self.entries.append(kwargs)


def auth(settings: Settings, subject: str, roles: list[Role]) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(settings, subject, roles)}"}


def make_outbox_client() -> tuple[TestClient, FakeQuotationSyncRepository, FakeAudit, Settings]:
    settings = make_settings()
    repository = FakeQuotationSyncRepository()
    audit = FakeAudit()
    app = create_app(settings, run_startup_checks=False)
    app.dependency_overrides[get_session] = lambda: FakeSession()
    app.dependency_overrides[get_ops_repository] = lambda: FakeOpsRepository()
    app.dependency_overrides[get_quotation_sync_repository] = lambda: repository
    app.dependency_overrides[get_audit_service] = lambda: audit
    return TestClient(app), repository, audit, settings


def test_admin_reads_activity_feed() -> None:
    client, repository, settings = make_client()
    headers = auth(settings, "admin-user", [Role.ADMIN])
    with client:
        response = client.get("/api/v1/ops/activity", params={"limit": 10}, headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["count"] == 1
        event = body["events"][0]
        assert event["event_type"] == "USER_LOGIN_SUCCEEDED"
        assert event["occurred_at"] == "2026-02-01T12:00:00+00:00"
        assert event["payload"] == {"source": "test"}
        assert repository.activity_limits == [10]

        invalid = client.get("/api/v1/ops/activity", params={"limit": 500}, headers=headers)
        assert invalid.status_code == 422


def test_activity_feed_is_hidden_from_sales_users() -> None:
    client, _, settings = make_client()
    headers = auth(settings, "sales-a", [Role.SALES])
    with client:
        response = client.get("/api/v1/ops/activity", headers=headers)
        assert response.status_code == 403
        assert "admin" in response.json()["detail"]


def test_viewer_reads_global_summary() -> None:
    client, repository, settings = make_client()
    headers = auth(settings, "viewer-user", [Role.VIEWER])
    with client:
        response = client.get("/api/v1/ops/summary", headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["tenders"] == 3
        assert response.json()["gross_invoiced"] == "2380.00"
        assert repository.summary_owners == [None]


def test_sales_summary_is_owner_scoped() -> None:
    client, repository, settings = make_client()
    headers = auth(settings, "sales-a", [Role.SALES])
    with client:
        response = client.get("/api/v1/ops/summary", headers=headers)
        assert response.status_code == 200, response.text
        assert repository.summary_owners == ["sales-a"]

        events = client.get("/api/v1/ops/activity", headers=headers)
        assert events.status_code == 403


def test_outbox_status_is_visible_to_admin_and_viewer_only() -> None:
    client, _, settings = make_client()
    admin = auth(settings, "admin-user", [Role.ADMIN])
    viewer = auth(settings, "viewer-user", [Role.VIEWER])
    sales = auth(settings, "sales-a", [Role.SALES])

    with client:
        admin_response = client.get("/api/v1/ops/outbox-status", headers=admin)
        viewer_response = client.get("/api/v1/ops/outbox-status", headers=viewer)
        sales_response = client.get("/api/v1/ops/outbox-status", headers=sales)

    assert admin_response.status_code == 200, admin_response.text
    assert admin_response.json() == {
        "quotation_sync": {
            "pending": 2,
            "processing": 1,
            "succeeded": 9,
            "failed": 0,
            "skipped": 1,
        },
        "total": 13,
    }
    assert viewer_response.status_code == 200
    assert sales_response.status_code == 403


def test_dashboard_endpoints_require_a_token() -> None:
    client, _, _ = make_client()
    with client:
        assert client.get("/api/v1/ops/activity").status_code == 401
        assert client.get("/api/v1/ops/summary").status_code == 401
        assert client.get("/api/v1/ops/outbox-status").status_code == 401
        assert client.get("/api/v1/ops/outbox").status_code == 401
        assert client.post(f"/api/v1/ops/outbox/{uuid4()}/retry").status_code == 401


def test_outbox_listing_returns_jobs_with_last_error_and_pagination() -> None:
    client, repository, _, settings = make_outbox_client()
    admin = auth(settings, "admin-user", [Role.ADMIN])

    with client:
        response = client.get(
            "/api/v1/ops/outbox",
            params={"status": "failed", "limit": 1, "offset": 0},
            headers=admin,
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["limit"] == 1
    assert body["offset"] == 0
    job = body["jobs"][0]
    assert job["offer_number"] == "AN-2026-0100"
    assert job["status"] == "failed"
    assert job["last_error"] == "ERPNext unavailable"
    assert job["attempts"] == 6
    assert job["created_at"] == "2026-02-01T11:00:00+00:00"
    assert repository.list_calls == [{"status": "failed", "limit": 1, "offset": 0}]


def test_outbox_listing_is_visible_to_admin_approver_and_viewer_only() -> None:
    client, _, _, settings = make_outbox_client()
    admin = auth(settings, "admin-user", [Role.ADMIN])
    approver = auth(settings, "approver-user", [Role.APPROVER])
    viewer = auth(settings, "viewer-user", [Role.VIEWER])
    sales = auth(settings, "sales-a", [Role.SALES])

    with client:
        admin_response = client.get("/api/v1/ops/outbox", headers=admin)
        approver_response = client.get("/api/v1/ops/outbox", headers=approver)
        viewer_response = client.get("/api/v1/ops/outbox", headers=viewer)
        sales_response = client.get("/api/v1/ops/outbox", headers=sales)
        invalid_status = client.get("/api/v1/ops/outbox", params={"status": "exploded"}, headers=admin)
        invalid_limit = client.get("/api/v1/ops/outbox", params={"limit": 0}, headers=admin)

    assert admin_response.status_code == 200, admin_response.text
    assert admin_response.json()["total"] == 2
    assert approver_response.status_code == 200
    assert viewer_response.status_code == 200
    assert sales_response.status_code == 403
    assert invalid_status.status_code == 422
    assert invalid_limit.status_code == 422


def test_admin_retries_a_failed_outbox_job() -> None:
    client, repository, audit, settings = make_outbox_client()
    admin = auth(settings, "admin-user", [Role.ADMIN])
    failed_id = repository.failed.id

    with client:
        response = client.post(f"/api/v1/ops/outbox/{failed_id}/retry", headers=admin)

    assert response.status_code == 200, response.text
    job = response.json()["job"]
    assert job["id"] == str(failed_id)
    assert job["status"] == "pending"
    assert job["attempts"] == 0
    assert job["last_error"] is None
    assert repository.retried == [failed_id]
    entry = audit.entries[-1]
    assert entry["event_type"] is AuditEventType.SYSTEM_OUTBOX_RETRIED
    assert entry["actor"] == "admin-user"
    assert entry["entity_id"] == str(failed_id)
    assert entry["payload"]["offer_number"] == "AN-2026-0100"
    assert entry["payload"]["previous_attempts"] == 6


def test_approver_can_retry_but_viewer_and_sales_cannot() -> None:
    client, repository, _, settings = make_outbox_client()
    failed_id = repository.failed.id
    approver = auth(settings, "approver-user", [Role.APPROVER])
    viewer = auth(settings, "viewer-user", [Role.VIEWER])
    sales = auth(settings, "sales-a", [Role.SALES])

    with client:
        allowed = client.post(f"/api/v1/ops/outbox/{failed_id}/retry", headers=approver)
        viewer_response = client.post(f"/api/v1/ops/outbox/{failed_id}/retry", headers=viewer)
        sales_response = client.post(f"/api/v1/ops/outbox/{failed_id}/retry", headers=sales)

    assert allowed.status_code == 200, allowed.text
    assert viewer_response.status_code == 403
    assert sales_response.status_code == 403


def test_retry_rejects_non_failed_and_unknown_jobs() -> None:
    client, repository, audit, settings = make_outbox_client()
    admin = auth(settings, "admin-user", [Role.ADMIN])
    pending_id = repository.pending.id
    missing_id = uuid4()

    with client:
        pending_response = client.post(f"/api/v1/ops/outbox/{pending_id}/retry", headers=admin)
        missing_response = client.post(f"/api/v1/ops/outbox/{missing_id}/retry", headers=admin)
        malformed_response = client.post("/api/v1/ops/outbox/not-a-uuid/retry", headers=admin)

    assert pending_response.status_code == 409
    assert "only failed jobs" in pending_response.text
    assert missing_response.status_code == 404
    assert malformed_response.status_code == 404
    assert repository.retried == []
    assert audit.entries == []
