"""External service integrations."""

from __future__ import annotations

from src.integrations.clamav_client import ClamAVError, ClamAVResult, ClamAVScanner
from src.integrations.erpnext_client import (
    ERPItem,
    ERPNextClient,
    ERPNextError,
    ERPNextNotConfigured,
    ERPQuotation,
    ERPSyncResult,
)
from src.integrations.minio_client import (
    MinioObjectStorage,
    ObjectStorageError,
    object_name_for_invoice,
    object_name_for_tender,
)
from src.integrations.n8n_client import (
    N8nClient,
    N8nConfigurationError,
    N8nDispatchResult,
    N8nError,
    N8nSignatureError,
)

__all__ = [
    "ClamAVError",
    "ClamAVResult",
    "ClamAVScanner",
    "ERPItem",
    "ERPNextClient",
    "ERPNextError",
    "ERPNextNotConfigured",
    "ERPQuotation",
    "ERPSyncResult",
    "MinioObjectStorage",
    "N8nClient",
    "N8nConfigurationError",
    "N8nDispatchResult",
    "N8nError",
    "N8nSignatureError",
    "ObjectStorageError",
    "object_name_for_invoice",
    "object_name_for_tender",
]
