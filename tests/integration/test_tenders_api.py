"""Tender list/detail/document endpoint tests with injected repositories."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from src.api.deps import get_object_storage, get_session, get_tender_repository
from src.api.security import Role, create_access_token
from src.config import Settings
from src.main import create_app
from src.models.enums import ProcurementProcedure, TenderSourceFormat, TenderStatus
from src.models.schemas import Tender
from src.repositories.tender_repository import StoredTender


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url=None,
        app_env="test",
        jwt_secret_key="tender-api-jwt-secret-with-32-bytes",
        api_admin_password="test-admin-password",
    )


class FakeSession:
    async def get(self, model: Any, key: Any) -> None:
        return None


class FakeTenderRepository:
    def __init__(self, stored: StoredTender) -> None:
        self.stored = stored
        self.list_calls: list[dict[str, Any]] = []

    async def get(self, tender_id: UUID) -> StoredTender | None:
        return self.stored if tender_id == self.stored.tender.id else None

    async def list_tenders(self, **kwargs: Any) -> list[StoredTender]:
        self.list_calls.append(kwargs)
        return [self.stored]

    async def count(self, **kwargs: Any) -> int:
        return 1


class FakeStorage:
    def __init__(self, *, present: bool = True, data: bytes = b"%PDF-1.7 fake") -> None:
        self.present = present
        self.data = data
        self.requested: list[str] = []

    async def exists(self, object_name: str) -> bool:
        return self.present

    async def get_bytes(self, object_name: str) -> bytes:
        self.requested.append(object_name)
        return self.data


def make_tender(
    *,
    owner: str | None = None,
    status: TenderStatus = TenderStatus.PUBLISHED,
) -> Tender:
    return Tender(
        tender_number="T-2026-001",
        title="Papierhandtücher und Spender",
        client_name="Stadt Musterstadt",
        procedure=ProcurementProcedure.OPEN_PROCEDURE,
        source_format=TenderSourceFormat.GAEB_X83,
        submission_deadline=datetime.now(UTC) + timedelta(days=14),
        status=status,
        owner=owner,
    )


def make_client(
    *,
    owner: str | None = None,
    source_object: str | None = "tenders/T-2026-001/tender.pdf",
    source_filename: str | None = "tender.pdf",
    storage_present: bool = True,
) -> tuple[TestClient, FakeTenderRepository, FakeStorage, Settings]:
    settings = make_settings()
    stored = StoredTender(
        tender=make_tender(owner=owner),
        source_filename=source_filename,
        source_object=source_object,
    )
    repository = FakeTenderRepository(stored)
    storage = FakeStorage(present=storage_present)
    app = create_app(settings, run_startup_checks=False)
    app.dependency_overrides[get_session] = lambda: FakeSession()
    app.dependency_overrides[get_tender_repository] = lambda: repository
    app.dependency_overrides[get_object_storage] = lambda: storage
    return TestClient(app), repository, storage, settings


def auth(settings: Settings, subject: str, roles: list[Role]) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(settings, subject, roles)}"}


def test_admin_can_list_and_open_tenders() -> None:
    client, repository, _, settings = make_client(owner="sales-a")
    headers = auth(settings, "admin-user", [Role.ADMIN])
    with client:
        listed = client.get("/api/v1/tenders", headers=headers)
        assert listed.status_code == 200, listed.text
        body = listed.json()
        assert body["count"] == 1
        assert body["limit"] == 50
        assert body["offset"] == 0
        assert body["tenders"][0]["tender_number"] == "T-2026-001"
        assert repository.list_calls[0]["owner"] is None

        detail = client.get(f"/api/v1/tenders/{repository.stored.tender.id}", headers=headers)
        assert detail.status_code == 200, detail.text
        assert detail.json()["tender"]["status"] == "PUBLISHED"
        assert detail.json()["document"] == {"filename": "tender.pdf", "available": True}

        document = client.get(
            f"/api/v1/tenders/{repository.stored.tender.id}/document",
            headers=headers,
        )
        assert document.status_code == 200
        assert document.headers["content-type"] == "application/pdf"
        assert 'filename="tender.pdf"' in document.headers["content-disposition"]
        assert document.content == b"%PDF-1.7 fake"


def test_status_filter_and_pagination_are_forwarded() -> None:
    client, repository, _, settings = make_client()
    headers = auth(settings, "admin-user", [Role.ADMIN])
    with client:
        response = client.get(
            "/api/v1/tenders",
            params={"status": "AWARDED", "limit": 10, "offset": 20},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        call = repository.list_calls[-1]
        assert call["status"] is TenderStatus.AWARDED
        assert call["limit"] == 10
        assert call["offset"] == 20
        assert response.json()["limit"] == 10
        assert response.json()["offset"] == 20

        invalid = client.get("/api/v1/tenders", params={"status": "NOPE"}, headers=headers)
        assert invalid.status_code == 422
        too_large = client.get("/api/v1/tenders", params={"limit": 500}, headers=headers)
        assert too_large.status_code == 422


def test_sales_users_only_see_their_own_tenders() -> None:
    client, repository, _, settings = make_client(owner="sales-a")
    headers = auth(settings, "sales-a", [Role.SALES])
    with client:
        listed = client.get("/api/v1/tenders", headers=headers)
        assert listed.status_code == 200, listed.text
        assert repository.list_calls[-1]["owner"] == "sales-a"

        detail = client.get(f"/api/v1/tenders/{repository.stored.tender.id}", headers=headers)
        assert detail.status_code == 200, detail.text

    other_client, other_repository, _, other_settings = make_client(owner="sales-b")
    other_headers = auth(other_settings, "sales-a", [Role.SALES])
    with other_client:
        forbidden = other_client.get(
            f"/api/v1/tenders/{other_repository.stored.tender.id}",
            headers=other_headers,
        )
        assert forbidden.status_code == 403
        assert "own" in forbidden.json()["detail"]

        document = other_client.get(
            f"/api/v1/tenders/{other_repository.stored.tender.id}/document",
            headers=other_headers,
        )
        assert document.status_code == 403


def test_viewer_can_read_tenders_and_documents() -> None:
    client, repository, _, settings = make_client(owner="sales-b")
    headers = auth(settings, "viewer-user", [Role.VIEWER])
    with client:
        listed = client.get("/api/v1/tenders", headers=headers)
        assert listed.status_code == 200, listed.text
        assert repository.list_calls[-1]["owner"] is None

        document = client.get(
            f"/api/v1/tenders/{repository.stored.tender.id}/document",
            headers=headers,
        )
        assert document.status_code == 200


def test_unknown_tender_and_missing_documents_return_404() -> None:
    client, _, _, settings = make_client()
    headers = auth(settings, "admin-user", [Role.ADMIN])
    with client:
        assert client.get(f"/api/v1/tenders/{uuid4()}", headers=headers).status_code == 404
        assert client.get(f"/api/v1/tenders/{uuid4()}/document", headers=headers).status_code == 404

    no_source, repository, _, settings = make_client(source_object=None)
    headers = auth(settings, "admin-user", [Role.ADMIN])
    with no_source:
        detail = no_source.get(f"/api/v1/tenders/{repository.stored.tender.id}", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["document"] == {"filename": "tender.pdf", "available": False}
        document = no_source.get(f"/api/v1/tenders/{repository.stored.tender.id}/document", headers=headers)
        assert document.status_code == 404

    missing_object, repository, _, settings = make_client(storage_present=False)
    headers = auth(settings, "admin-user", [Role.ADMIN])
    with missing_object:
        document = missing_object.get(
            f"/api/v1/tenders/{repository.stored.tender.id}/document", headers=headers
        )
        assert document.status_code == 404


def test_document_filename_is_encoded_for_non_ascii_names() -> None:
    client, repository, _, settings = make_client(
        source_filename="Handtücher.pdf",
        source_object="tenders/T-2026-001/Handtücher.pdf",
    )
    headers = auth(settings, "admin-user", [Role.ADMIN])
    with client:
        document = client.get(
            f"/api/v1/tenders/{repository.stored.tender.id}/document",
            headers=headers,
        )
        assert document.status_code == 200
        disposition = document.headers["content-disposition"]
        assert "filename*=UTF-8''Handt%C3%BCcher.pdf" in disposition
        assert 'filename="Handt?cher.pdf"' in disposition


def test_tender_endpoints_require_a_token() -> None:
    client, _, _, _ = make_client()
    with client:
        assert client.get("/api/v1/tenders").status_code == 401
        assert client.get(f"/api/v1/tenders/{uuid4()}").status_code == 401
