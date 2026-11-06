"""Optional smoke tests against real MinIO and ClamAV services.

Set ``YASMINA_TEST_SERVICES=1`` with the regular MINIO_*/CLAMAV_* environment
to exercise the object-store and malware-scanner round trips. CI sets this flag;
normal local runs skip the tests.
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from src.config import Settings
from src.integrations.clamav_client import ClamAVScanner
from src.integrations.minio_client import MinioObjectStorage

SERVICE_TESTS = os.getenv("YASMINA_TEST_SERVICES") == "1"

pytestmark = pytest.mark.skipif(
    not SERVICE_TESTS,
    reason="set YASMINA_TEST_SERVICES=1 to run MinIO/ClamAV smoke tests",
)


def settings() -> Settings:
    return Settings(_env_file=None, app_env="test")


async def test_minio_object_roundtrip() -> None:
    storage = MinioObjectStorage(settings())
    key = f"ci-smoke/{uuid4().hex}.txt"
    payload = b"yasmina object store roundtrip"

    await storage.put_bytes(key, payload, "text/plain")
    try:
        assert await storage.get_bytes(key) == payload
        assert await storage.exists(key) is True
    finally:
        await storage.delete(key)


async def test_clamav_scans_clean_payload() -> None:
    scanner = ClamAVScanner(
        os.getenv("CLAMAV_HOST", "localhost"),
        int(os.getenv("CLAMAV_PORT", "3310")),
        timeout=30.0,
    )

    result = await scanner.scan_bytes(b"yasmina clean scan")

    assert result.clean is True
