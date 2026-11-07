"""Encrypted PostgreSQL backups uploaded to a dedicated MinIO bucket.

``pg_dump`` writes a custom-format archive to stdout; the archive is encrypted
with Fernet (AES-128-CBC + HMAC-SHA256) before it leaves the process, then
uploaded to ``BACKUP_BUCKET``. Only ``BACKUP_RETENTION_DAYS`` worth of backups
is kept; pruning runs after a successful upload and never deletes the new key.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import logging
import secrets
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from src.config import Settings
from src.integrations.minio_client import MinioObjectStorage, ObjectStorageError, StoredObject

__all__ = [
    "BackupError",
    "BackupResult",
    "backup_object_key",
    "decrypt_backup",
    "encrypt_backup",
    "generate_backup_key",
    "prunable_objects",
    "run_postgres_backup",
]

DumpRunner = Callable[[list[str]], Awaitable[bytes]]

logger = logging.getLogger("yasmina.backup")


class BackupError(RuntimeError):
    """Raised when a backup cannot be dumped, encrypted or uploaded."""


@dataclass(frozen=True, slots=True)
class BackupResult:
    key: str
    bucket: str
    size_bytes: int
    sha256: str
    created_at: datetime
    pruned: tuple[str, ...] = ()
    dry_run: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "bucket": self.bucket,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "created_at": self.created_at.isoformat(),
            "pruned": list(self.pruned),
            "dry_run": self.dry_run,
        }


def generate_backup_key() -> str:
    """Return a fresh URL-safe Fernet key for ``BACKUP_ENCRYPTION_KEY``."""

    return Fernet.generate_key().decode("ascii")


def _backup_key_bytes(settings: Settings) -> bytes:
    secret = settings.backup_encryption_key
    if secret is None or not secret.get_secret_value().strip():
        raise BackupError(
            "BACKUP_ENCRYPTION_KEY is not configured; generate one with "
            "`python -m src.admin generate-backup-key`"
        )
    key = secret.get_secret_value().strip()
    try:
        raw = base64.urlsafe_b64decode(key.encode("ascii"))
    except (binascii.Error, ValueError) as exc:
        raise BackupError("BACKUP_ENCRYPTION_KEY is not a valid Fernet key") from exc
    if len(raw) != 32:
        raise BackupError("BACKUP_ENCRYPTION_KEY must decode to 32 bytes")
    return key.encode("ascii")


def encrypt_backup(settings: Settings, payload: bytes) -> bytes:
    try:
        return Fernet(_backup_key_bytes(settings)).encrypt(payload)
    except Exception as exc:  # pragma: no cover - key format validated above
        raise BackupError(f"backup encryption failed: {exc}") from exc


def decrypt_backup(settings: Settings, payload: bytes) -> bytes:
    try:
        return Fernet(_backup_key_bytes(settings)).decrypt(payload)
    except InvalidToken as exc:
        raise BackupError("backup cannot be decrypted with the configured key") from exc


def backup_object_key(settings: Settings, now: datetime) -> str:
    prefix = settings.backup_prefix.strip().strip("/") or "postgres"
    stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}/{now.astimezone(UTC):%Y/%m/%d}/yasmina-{stamp}-{secrets.token_hex(4)}.dump.fernet"


def prunable_objects(
    objects: list[StoredObject],
    *,
    now: datetime,
    retention_days: int,
) -> list[StoredObject]:
    if retention_days <= 0:
        return []
    cutoff = now - timedelta(days=retention_days)
    prunable: list[StoredObject] = []
    for obj in objects:
        modified = obj.last_modified
        if modified.tzinfo is None:
            modified = modified.replace(tzinfo=UTC)
        if modified < cutoff:
            prunable.append(obj)
    return sorted(prunable, key=lambda item: item.last_modified)


async def _run_pg_dump(command: list[str], *, timeout: int) -> bytes:
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise BackupError(
            f"pg_dump binary {command[0]!r} not found; install postgresql-client or set PG_DUMP_BINARY"
        ) from exc
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise BackupError(f"pg_dump exceeded {timeout} seconds") from exc
    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()[-800:]
        raise BackupError(f"pg_dump failed with exit code {process.returncode}: {detail}")
    if not stdout:
        raise BackupError("pg_dump produced an empty archive")
    return stdout


async def run_postgres_backup(
    settings: Settings,
    *,
    storage: MinioObjectStorage | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
    dump_runner: DumpRunner | None = None,
) -> BackupResult:
    """Dump, encrypt and upload one PostgreSQL backup, then prune old ones."""

    current = (now or datetime.now(UTC)).astimezone(UTC)
    object_storage = storage or MinioObjectStorage(settings, bucket=settings.backup_bucket)
    key = backup_object_key(settings, current)
    prefix = settings.backup_prefix.strip().strip("/") or "postgres"

    if dry_run:
        candidates = await _prunable(object_storage, current, settings.backup_retention_days, prefix)
        return BackupResult(
            key=key,
            bucket=object_storage.bucket,
            size_bytes=0,
            sha256="",
            created_at=current,
            pruned=tuple(obj.name for obj in candidates),
            dry_run=True,
        )

    binary = settings.pg_dump_binary
    if dump_runner is None and shutil.which(binary) is None:
        raise BackupError(
            f"pg_dump binary {binary!r} not found; install postgresql-client or set PG_DUMP_BINARY"
        )
    command = [
        binary,
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "--dbname",
        settings.asyncpg_dsn,
    ]
    runner: DumpRunner = dump_runner or (
        lambda args: _run_pg_dump(args, timeout=settings.backup_timeout_seconds)
    )
    try:
        archive = await runner(command)
    except BackupError:
        raise
    except Exception as exc:
        raise BackupError(f"pg_dump failed: {exc}") from exc

    encrypted = encrypt_backup(settings, archive)
    try:
        await object_storage.put_bytes(key, encrypted, "application/octet-stream")
    except Exception as exc:
        raise BackupError(f"backup upload failed: {exc}") from exc

    candidates = await _prunable(object_storage, current, settings.backup_retention_days, prefix)
    pruned: list[str] = []
    for candidate in candidates:
        if candidate.name == key:
            continue
        try:
            await object_storage.delete(candidate.name)
        except ObjectStorageError as exc:
            # Retention is best-effort; the new backup is already safely stored.
            logger.warning(
                "backup retention could not delete %s: %s",
                candidate.name,
                exc,
                extra={"event": "backup_prune_failed", "key": candidate.name},
            )
            continue
        pruned.append(candidate.name)

    return BackupResult(
        key=key,
        bucket=object_storage.bucket,
        size_bytes=len(encrypted),
        sha256=hashlib.sha256(encrypted).hexdigest(),
        created_at=current,
        pruned=tuple(pruned),
    )


async def _prunable(
    storage: MinioObjectStorage,
    now: datetime,
    retention_days: int,
    prefix: str,
) -> list[StoredObject]:
    if retention_days <= 0:
        return []
    try:
        objects = await storage.list_objects(prefix=f"{prefix}/")
    except ObjectStorageError as exc:
        # A missing bucket or a transient listing failure must not block a new
        # backup: retention is retried on the next run.
        logger.warning(
            "cannot list existing backups; skipping retention: %s",
            exc,
            extra={"event": "backup_retention_list_failed"},
        )
        return []
    return prunable_objects(objects, now=now, retention_days=retention_days)
