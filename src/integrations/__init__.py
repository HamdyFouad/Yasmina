"""External service integrations."""

from __future__ import annotations

from src.integrations.clamav_client import ClamAVError, ClamAVResult, ClamAVScanner
from src.integrations.minio_client import (
    MinioObjectStorage,
    ObjectStorageError,
    object_name_for_invoice,
    object_name_for_tender,
)

__all__ = [
    "ClamAVError",
    "ClamAVResult",
    "ClamAVScanner",
    "MinioObjectStorage",
    "ObjectStorageError",
    "object_name_for_invoice",
    "object_name_for_tender",
]
