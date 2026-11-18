"""Notification center API tests with a mocked live aggregate."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from src.api.deps import get_notification_repository, get_session
from src.api.security import Role, create_access_token
from src.config import Settings
from src.main import create_app

SETTINGS = Settings(
    _env_file=None,
    database_url=None,
    app_env="test",
    jwt_secret_key="notifications-jwt-secret-with-32-bytes",
    api_admin_password="test-admin-password",
)
ADMIN = {"Authorization": f"Bearer {create_access_token(SETTINGS, 'admin', [Role.ADMIN])}"}
VIEWER = {"Authorization": f"Bearer {create_access_token(SETTINGS, 'viewer', [Role.VIEWER])}"}
SALES = {"Authorization": f"Bearer {create_access_token(SETTINGS, 'sales', [Role.SALES])}"}


class FakeSession:
    async def get(self, model: Any, key: Any) -> None:
        return None


class FakeNotifications:
    async def notifications(self) -> dict[str, Any]:
        return {
            "alerts": [
                {
                    "id": "procurement-exceptions",
                    "severity": "critical",
                    "title": "Procurement exceptions",
                    "message": "2 supplier invoice(s) need review.",
                    "count": 2,
                    "href": "/procurement",
                }
            ],
            "count": 1,
        }


def test_notification_center_is_available_to_operators_only() -> None:
    app = create_app(SETTINGS, run_startup_checks=False)
    app.dependency_overrides[get_session] = lambda: FakeSession()
    app.dependency_overrides[get_notification_repository] = lambda: FakeNotifications()
    client = TestClient(app)
    with client:
        admin = client.get("/api/v1/ops/notifications", headers=ADMIN)
        viewer = client.get("/api/v1/ops/notifications", headers=VIEWER)
        sales = client.get("/api/v1/ops/notifications", headers=SALES)

    assert admin.status_code == 200, admin.text
    assert admin.json()["count"] == 1
    assert admin.json()["alerts"][0]["href"] == "/procurement"
    assert viewer.status_code == 200
    assert sales.status_code == 403
