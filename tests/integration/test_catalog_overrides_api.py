"""Admin catalogue override API tests with an injected repository."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from src.api.deps import get_catalog_repository, get_session
from src.api.security import Role, create_access_token
from src.config import Settings
from src.main import create_app
from src.models.enums import GloveSize
from src.repositories.catalog_repository import MERGEABLE_FIELDS, CatalogOverrideView


class FakeSession:
    async def get(self, model: Any, key: Any) -> None:
        return None


class FakeCatalogRepository:
    def __init__(self) -> None:
        now = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)
        self.view = CatalogOverrideView(
            id=uuid4(),
            supplier_id=uuid4(),
            supplier_name="BayerClean",
            article_number="ART-100",
            description="Nitrilhandschuhe blau Gr. M",
            material=None,
            color=None,
            glove_size=GloveSize.M,
            unit=None,
            unit_price_net=None,
            pack_size=None,
            manual_overrides=("description",),
            updated_at=now,
        )
        self.list_calls: list[dict[str, Any]] = []
        self.set_calls: list[tuple[tuple[str, ...], str]] = []
        self.clear_calls: list[tuple[str, ...] | None] = []

    async def list_manual_overrides(self, **kwargs: Any) -> tuple[list[CatalogOverrideView], int]:
        self.list_calls.append(kwargs)
        return [self.view], 1

    async def get_by_supplier_article(
        self, supplier_name: str, article_number: str
    ) -> CatalogOverrideView | None:
        if supplier_name == self.view.supplier_name and article_number == self.view.article_number:
            return self.view
        return None

    async def set_manual_overrides(
        self,
        article_id: Any,
        fields: list[str],
        *,
        mode: str = "add",
    ) -> tuple[str, ...]:
        unknown = [field for field in fields if field not in MERGEABLE_FIELDS]
        if unknown:
            raise ValueError(f"unknown catalogue field(s): {', '.join(sorted(unknown))}")
        self.set_calls.append((tuple(fields), mode))
        if mode == "replace":
            overrides = tuple(fields)
        else:
            overrides = tuple(dict.fromkeys((*self.view.manual_overrides, *fields)))
        self.view = replace(self.view, manual_overrides=overrides)
        return overrides

    async def clear_manual_overrides(
        self,
        article_id: Any,
        fields: list[str] | None = None,
    ) -> tuple[str, ...]:
        self.clear_calls.append(None if fields is None else tuple(fields))
        overrides: tuple[str, ...] = ()
        if fields is not None:
            overrides = tuple(item for item in self.view.manual_overrides if item not in fields)
        self.view = replace(self.view, manual_overrides=overrides)
        return overrides


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url=None,
        app_env="test",
        jwt_secret_key="catalog-overrides-jwt-secret-32-bytes",
        api_admin_password="test-admin-password",
    )


def make_client() -> tuple[TestClient, FakeCatalogRepository, Settings]:
    settings = make_settings()
    repository = FakeCatalogRepository()
    app = create_app(settings, run_startup_checks=False)
    app.dependency_overrides[get_session] = lambda: FakeSession()
    app.dependency_overrides[get_catalog_repository] = lambda: repository
    return TestClient(app), repository, settings


def auth(settings: Settings, role: Role) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(settings, 'admin-user', [role])}"}


def test_admin_lists_overrides_with_filters() -> None:
    client, repository, settings = make_client()
    with client:
        response = client.get(
            "/api/v1/admin/catalog/overrides",
            params={"supplier": "Bayer", "article_number": "ART-", "limit": 10, "offset": 0},
            headers=auth(settings, Role.ADMIN),
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == 1
    assert body["overrides"][0]["manual_overrides"] == ["description"]
    assert repository.list_calls[0]["supplier"] == "Bayer"


def test_non_admin_cannot_read_or_change_overrides() -> None:
    client, _, settings = make_client()
    with client:
        read = client.get(
            "/api/v1/admin/catalog/overrides",
            headers=auth(settings, Role.SALES),
        )
        write = client.post(
            "/api/v1/admin/catalog/overrides",
            json={
                "supplier_name": "BayerClean",
                "article_number": "ART-100",
                "fields": ["description"],
            },
            headers=auth(settings, Role.SALES),
        )

    assert read.status_code == 403
    assert write.status_code == 403


def test_admin_pins_and_clears_overrides() -> None:
    client, repository, settings = make_client()
    with client:
        pinned = client.post(
            "/api/v1/admin/catalog/overrides",
            json={
                "supplier_name": "BayerClean",
                "article_number": "ART-100",
                "fields": ["unit_price_net"],
                "mode": "add",
            },
            headers=auth(settings, Role.ADMIN),
        )
        replaced = client.post(
            "/api/v1/admin/catalog/overrides",
            json={
                "supplier_name": "BayerClean",
                "article_number": "ART-100",
                "fields": ["color"],
                "mode": "replace",
            },
            headers=auth(settings, Role.ADMIN),
        )
        cleared = client.post(
            "/api/v1/admin/catalog/overrides",
            json={
                "supplier_name": "BayerClean",
                "article_number": "ART-100",
                "fields": [],
                "mode": "clear",
            },
            headers=auth(settings, Role.ADMIN),
        )

    assert pinned.status_code == 200, pinned.text
    assert pinned.json()["override"]["manual_overrides"] == ["description", "unit_price_net"]
    assert replaced.json()["override"]["manual_overrides"] == ["color"]
    assert cleared.json()["override"]["manual_overrides"] == []
    assert repository.set_calls == [
        (("unit_price_net",), "add"),
        (("color",), "replace"),
    ]
    assert repository.clear_calls == [None]


def test_unknown_article_and_field_are_rejected() -> None:
    client, _, settings = make_client()
    with client:
        missing = client.post(
            "/api/v1/admin/catalog/overrides",
            json={
                "supplier_name": "Missing",
                "article_number": "NOPE",
                "fields": ["description"],
            },
            headers=auth(settings, Role.ADMIN),
        )
        invalid = client.post(
            "/api/v1/admin/catalog/overrides",
            json={
                "supplier_name": "BayerClean",
                "article_number": "ART-100",
                "fields": ["not_a_field"],
            },
            headers=auth(settings, Role.ADMIN),
        )

    assert missing.status_code == 404
    assert invalid.status_code == 422
