"""Encrypted pg_dump backups: encryption, upload, retention and failure modes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from src.config import Settings
from src.integrations.minio_client import StoredObject
from src.services.backup_service import (
    BackupError,
    BackupResult,
    backup_object_key,
    decrypt_backup,
    encrypt_backup,
    generate_backup_key,
    prunable_objects,
    run_postgres_backup,
)

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "database_url": "postgresql+asyncpg://yasmina:secret@localhost:5432/yasmina",
        "backup_bucket": "yasmina-backups",
        "backup_prefix": "postgres",
        "backup_encryption_key": generate_backup_key(),
        "backup_retention_days": 14,
        "pg_dump_binary": "pg_dump",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


class FakeStorage:
    bucket = "yasmina-backups"

    def __init__(self, objects: list[StoredObject] | None = None) -> None:
        self.objects: dict[str, StoredObject] = {obj.name: obj for obj in objects or []}
        self.payloads: dict[str, bytes] = {}
        self.deleted: list[str] = []

    async def put_bytes(self, name: str, data: bytes, content_type: str) -> str:
        self.objects[name] = StoredObject(name=name, size=len(data), last_modified=NOW)
        self.payloads[name] = data
        assert content_type == "application/octet-stream"
        return name

    async def list_objects(self, prefix: str = "") -> list[StoredObject]:
        return [obj for name, obj in self.objects.items() if name.startswith(prefix)]

    async def delete(self, name: str) -> None:
        self.deleted.append(name)
        self.objects.pop(name, None)


def test_backup_key_is_valid_fernet_key() -> None:
    key = generate_backup_key()

    assert len(key) == 44
    assert encrypt_backup(make_settings(), b"payload")


def test_secret_is_encrypted_and_round_trips() -> None:
    settings = make_settings()
    archive = b"PGDMP\x00binary custom dump"

    encrypted = encrypt_backup(settings, archive)

    assert encrypted != archive
    assert decrypt_backup(settings, encrypted) == archive


def test_missing_or_wrong_key_is_rejected() -> None:
    with pytest.raises(BackupError, match="BACKUP_ENCRYPTION_KEY"):
        encrypt_backup(make_settings(backup_encryption_key=None), b"payload")

    encrypted = encrypt_backup(make_settings(), b"payload")
    with pytest.raises(BackupError, match="cannot be decrypted"):
        decrypt_backup(make_settings(), encrypted)


def test_backup_object_key_layout() -> None:
    key = backup_object_key(make_settings(), NOW)

    assert key.startswith("postgres/2026/01/15/yasmina-20260115T120000Z-")
    assert key.endswith(".dump.fernet")


def test_prunable_objects_respects_retention() -> None:
    old = StoredObject("postgres/old.dump.fernet", 1, NOW - timedelta(days=40))
    recent = StoredObject("postgres/recent.dump.fernet", 1, NOW - timedelta(days=2))

    assert prunable_objects([recent, old], now=NOW, retention_days=14) == [old]
    assert prunable_objects([old], now=NOW, retention_days=0) == []


async def test_backup_encrypts_uploads_and_prunes() -> None:
    settings = make_settings()
    old = StoredObject("postgres/2025/01/01/yasmina-old.dump.fernet", 10, NOW - timedelta(days=40))
    recent = StoredObject("postgres/2026/01/14/yasmina-recent.dump.fernet", 10, NOW - timedelta(days=1))
    storage = FakeStorage([old, recent])
    archive = b"PGDMP encrypted archive bytes"
    calls: list[list[str]] = []

    async def fake_dump(command: list[str]) -> bytes:
        calls.append(command)
        return archive

    result = await run_postgres_backup(settings, storage=storage, now=NOW, dump_runner=fake_dump)  # type: ignore[arg-type]

    assert calls and calls[0][1] == "--format=custom"
    assert "--no-owner" in calls[0]
    assert calls[0][-1].startswith("postgresql://")
    assert result.bucket == "yasmina-backups"
    assert result.size_bytes == len(storage.payloads[result.key])
    assert result.key in storage.payloads
    assert decrypt_backup(settings, storage.payloads[result.key]) == archive
    assert result.pruned == (old.name,)
    assert storage.deleted == [old.name]
    assert recent.name in storage.objects
    assert result.key in storage.objects


async def test_dry_run_does_not_dump_or_upload() -> None:
    settings = make_settings()
    old = StoredObject("postgres/2025/01/01/yasmina-old.dump.fernet", 10, NOW - timedelta(days=40))
    storage = FakeStorage([old])

    async def failing_dump(command: list[str]) -> bytes:  # pragma: no cover - must not run
        raise AssertionError("dry run must not execute pg_dump")

    result = await run_postgres_backup(
        settings,
        storage=storage,
        now=NOW,
        dry_run=True,
        dump_runner=failing_dump,
    )

    assert result.dry_run is True
    assert result.size_bytes == 0
    assert result.sha256 == ""
    assert result.pruned == (old.name,)
    assert storage.payloads == {}
    assert storage.deleted == []


async def test_missing_pg_dump_binary_is_reported() -> None:
    settings = make_settings(pg_dump_binary="yasmina-missing-pg-dump")

    with pytest.raises(BackupError, match="not found"):
        await run_postgres_backup(settings, storage=FakeStorage(), now=NOW)  # type: ignore[arg-type]


async def test_upload_failure_is_wrapped() -> None:
    class BrokenStorage(FakeStorage):
        async def put_bytes(self, name: str, data: bytes, content_type: str) -> str:
            raise RuntimeError("bucket unavailable")

    settings = make_settings()

    async def fake_dump(command: list[str]) -> bytes:
        return b"PGDMP"

    with pytest.raises(BackupError, match="upload failed"):
        await run_postgres_backup(
            settings,
            storage=BrokenStorage(),  # type: ignore[arg-type]
            now=NOW,
            dump_runner=fake_dump,
        )


def test_backup_result_serialises() -> None:
    payload: dict[str, Any] = BackupResult(
        key="postgres/backup.dump.fernet",
        bucket="yasmina-backups",
        size_bytes=10,
        sha256="a" * 64,
        created_at=NOW,
        pruned=("postgres/old.dump.fernet",),
    ).as_dict()

    assert payload["pruned"] == ["postgres/old.dump.fernet"]
    assert payload["created_at"].startswith("2026-01-15T12:00:00")
