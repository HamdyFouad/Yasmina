"""MinIO object-storage integration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any

from src.config import Settings

__all__ = ["MinioObjectStorage", "ObjectStorageError", "StoredObject"]


class ObjectStorageError(RuntimeError):
    """Raised when an object cannot be stored or retrieved."""


@dataclass(frozen=True, slots=True)
class StoredObject:
    name: str
    size: int
    last_modified: datetime


def _endpoint(settings: Settings) -> tuple[str, bool]:
    raw = settings.minio_endpoint.strip()
    secure = settings.minio_secure or raw.startswith("https://")
    endpoint = raw.removeprefix("https://").removeprefix("http://").rstrip("/")
    return endpoint, secure


class MinioObjectStorage:
    """Thin async wrapper around the synchronous MinIO SDK."""

    def __init__(self, settings: Settings, *, bucket: str | None = None) -> None:
        try:
            from minio import Minio
        except ImportError as exc:  # pragma: no cover - mandatory dependency
            raise ObjectStorageError("minio client is not installed") from exc

        endpoint, secure = _endpoint(settings)
        self.bucket = bucket or settings.minio_bucket
        self._client = Minio(
            endpoint,
            access_key=settings.minio_access_key.get_secret_value(),
            secret_key=settings.minio_secret_key.get_secret_value(),
            secure=secure,
        )

    async def ensure_bucket(self) -> None:
        try:
            exists = await asyncio.to_thread(self._client.bucket_exists, self.bucket)
            if not exists:
                await asyncio.to_thread(self._client.make_bucket, self.bucket)
        except Exception as exc:
            raise ObjectStorageError(f"cannot ensure MinIO bucket {self.bucket!r}: {exc}") from exc

    async def put_bytes(self, object_name: str, data: bytes, content_type: str) -> str:
        try:
            await self.ensure_bucket()
            await asyncio.to_thread(
                self._client.put_object,
                self.bucket,
                object_name,
                BytesIO(data),
                len(data),
                content_type=content_type,
            )
        except ObjectStorageError:
            raise
        except Exception as exc:
            raise ObjectStorageError(f"cannot store object {object_name!r}: {exc}") from exc
        return object_name

    async def get_bytes(self, object_name: str) -> bytes:
        try:
            response: Any = await asyncio.to_thread(self._client.get_object, self.bucket, object_name)
            try:
                return await asyncio.to_thread(response.read)
            finally:
                response.close()
                response.release_conn()
        except Exception as exc:
            raise ObjectStorageError(f"cannot read object {object_name!r}: {exc}") from exc

    async def exists(self, object_name: str) -> bool:
        try:
            await asyncio.to_thread(self._client.stat_object, self.bucket, object_name)
            return True
        except Exception:
            return False

    async def delete(self, object_name: str) -> None:
        try:
            await asyncio.to_thread(self._client.remove_object, self.bucket, object_name)
        except Exception as exc:
            raise ObjectStorageError(f"cannot delete object {object_name!r}: {exc}") from exc

    async def list_objects(self, prefix: str = "") -> list[StoredObject]:
        try:
            objects = await asyncio.to_thread(
                lambda: list(self._client.list_objects(self.bucket, prefix=prefix, recursive=True))
            )
        except Exception as exc:
            raise ObjectStorageError(f"cannot list objects in {self.bucket!r}: {exc}") from exc
        return [
            StoredObject(
                name=obj.object_name,
                size=int(getattr(obj, "size", 0) or 0),
                last_modified=obj.last_modified,
            )
            for obj in objects
        ]

    async def delete_many(self, object_names: list[str]) -> int:
        deleted = 0
        for name in object_names:
            await self.delete(name)
            deleted += 1
        return deleted

    async def presigned_get_url(self, object_name: str, *, expires_seconds: int = 3600) -> str:
        try:
            return await asyncio.to_thread(
                self._client.presigned_get_object,
                self.bucket,
                object_name,
                expires=timedelta(seconds=expires_seconds),
            )
        except Exception as exc:
            raise ObjectStorageError(f"cannot presign object {object_name!r}: {exc}") from exc


def object_name_for_tender(tender_id: Any, filename: str) -> str:
    safe = Path(filename).name.replace("\\", "_").replace("/", "_") or "upload"
    return f"tenders/{tender_id}/{safe}"


def object_name_for_invoice(offer_number: str, extension: str) -> str:
    safe = "".join(char if char.isalnum() or char in "._-" else "_" for char in offer_number)
    safe = safe[:128] or "offer"
    return f"offers/{safe}/invoice.{extension}"
