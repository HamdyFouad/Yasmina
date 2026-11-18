"""Audit log viewer and export API tests."""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from src.api.deps import (
    get_audit_log_repository,
    get_audit_service,
    get_session,
)
from src.api.security import Role, create_access_token
from src.config import Settings
from src.main import create_app
from src.repositories.audit_repository import AuditLogView

SETTINGS = Settings(
    _env_file=None,
    database_url=None,
    app_env="test",
    jwt_secret_key="audit-jwt-secret-with-at-least-32-bytes",
    api_admin_password="test-admin-password",
)
ADMIN = {"Authorization": f"Bearer {create_access_token(SETTINGS, 'admin', [Role.ADMIN])}"}
VIEWER = {"Authorization": f"Bearer {create_access_token(SETTINGS, 'viewer', [Role.VIEWER])}"}
ROW = AuditLogView(
    id=11,
    occurred_at=datetime(2026, 2, 1, 10, 0, tzinfo=UTC),
    event_type="USER_LOGIN_SUCCEEDED",
    actor="admin",
    entity_type="user",
    entity_id="admin",
    payload={"ip_address": "127.0.0.1"},
    prev_hash=None,
    entry_hash="a" * 64,
    signature=None,
)


class FakeSession:
    async def get(self, model: Any, key: Any) -> None:
        return None


class FakeAuditLogs:
    async def list_logs(self, *, limit: int, offset: int, **filters: Any) -> list[AuditLogView]:
        return [ROW]

    async def count_logs(self, **filters: Any) -> int:
        return 1

    async def iter_export(self, *, batch_size: int = 1000, **filters: Any):
        yield ROW


class FakeAudit:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    async def append(self, **kwargs: Any) -> None:
        self.entries.append(kwargs)


def make_client() -> tuple[TestClient, FakeAudit]:
    app = create_app(SETTINGS, run_startup_checks=False)
    audit = FakeAudit()
    app.dependency_overrides[get_session] = lambda: FakeSession()
    app.dependency_overrides[get_audit_log_repository] = lambda: FakeAuditLogs()
    app.dependency_overrides[get_audit_service] = lambda: audit
    return TestClient(app), audit


def test_audit_viewer_is_admin_only_and_returns_events() -> None:
    client, _ = make_client()
    with client:
        listed = client.get("/api/v1/audit/logs", headers=ADMIN)
        forbidden = client.get("/api/v1/audit/logs", headers=VIEWER)
        types = client.get("/api/v1/audit/event-types", headers=ADMIN)

    assert listed.status_code == 200, listed.text
    assert listed.json()["logs"][0]["event_type"] == "USER_LOGIN_SUCCEEDED"
    assert listed.json()["count"] == 1
    assert forbidden.status_code == 403
    assert types.status_code == 200
    assert "SYSTEM_AUDIT_EXPORTED" in types.json()["event_types"]


def test_audit_csv_and_xlsx_exports_stream_valid_files() -> None:
    client, audit = make_client()
    with client:
        csv_response = client.get("/api/v1/audit/logs/export?format=csv", headers=ADMIN)
        xlsx_response = client.get("/api/v1/audit/logs/export?format=xlsx", headers=ADMIN)

    assert csv_response.status_code == 200, csv_response.text
    assert csv_response.headers["content-type"].startswith("text/csv")
    assert "USER_LOGIN_SUCCEEDED" in csv_response.text
    assert "attachment;" in csv_response.headers["content-disposition"]

    assert xlsx_response.status_code == 200, xlsx_response.text
    assert xlsx_response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    with zipfile.ZipFile(io.BytesIO(xlsx_response.content)) as workbook:
        assert "xl/worksheets/sheet1.xml" in workbook.namelist()
        assert "USER_LOGIN_SUCCEEDED" in workbook.read("xl/worksheets/sheet1.xml").decode()

    assert [entry["event_type"].value for entry in audit.entries] == [
        "SYSTEM_AUDIT_EXPORTED",
        "SYSTEM_AUDIT_EXPORTED",
    ]
