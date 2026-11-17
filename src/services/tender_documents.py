"""Rehydrate a persisted tender's GAEB document from object storage."""

from __future__ import annotations

import tempfile
from pathlib import Path
from uuid import uuid4

from starlette.concurrency import run_in_threadpool

from src.integrations.minio_client import MinioObjectStorage, ObjectStorageError
from src.parsers.gaeb_parser import parse_gaeb
from src.repositories.tender_repository import StoredTender

__all__ = ["TenderDocumentError", "load_tender_document"]


class TenderDocumentError(RuntimeError):
    """Raised when the stored source cannot be read or parsed."""


async def load_tender_document(
    stored: StoredTender,
    storage: MinioObjectStorage,
) -> StoredTender:
    """Return ``stored`` with its GAEB document parsed from MinIO when needed.

    Uploads persist the object name but not the parsed pyGAEB document. Pricing
    does not need it, while GAEB ``.x84`` export does, so the document is
    rehydrated lazily from the archived source object.
    """

    if stored.document is not None or not stored.source_object:
        return stored

    suffix = Path(stored.source_filename or stored.source_object).suffix.lower()
    if suffix not in {".x83", ".d83"}:
        return stored

    try:
        data = await storage.get_bytes(stored.source_object)
    except ObjectStorageError as exc:
        raise TenderDocumentError(str(exc)) from exc

    tmp = Path(tempfile.gettempdir()) / f"{uuid4().hex}{suffix}"
    tmp.write_bytes(data)
    try:
        parsed = await run_in_threadpool(parse_gaeb, tmp)
    except Exception as exc:
        raise TenderDocumentError(f"cannot rehydrate GAEB source: {exc}") from exc
    finally:
        tmp.unlink(missing_ok=True)

    return StoredTender(
        tender=stored.tender,
        document=parsed.document,
        source_filename=stored.source_filename,
        source_path=stored.source_path,
        source_object=stored.source_object,
    )
