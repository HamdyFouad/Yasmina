"""Operations dashboard endpoint tests (activity feed and KPI aggregates)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from src.api.deps import get_ops_repository, get_quotation_sync_repository, get_session
from src.api.security import Role, create_access_token
from src.config import Settings
from src.main import create_app
from src.repositories.ops_repository import ActivityEvent


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
    return TestClient(app), repository, settings


class FakeQuotationSyncRepository:
    async def status_counts(self) -> dict[str, int]:
        return {
            "pending": 2,
            "processing": 1,
            "succeeded": 9,
            "failed": 0,
            "skipped": 1,
        }


def auth(settings: Settings, subject: str, roles: list[Role]) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(settings, subject, roles)}"}


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
