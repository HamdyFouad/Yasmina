"""Shared pytest configuration for Project Yasmina."""

from __future__ import annotations

import shutil
import sys
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def workspace_tmp() -> Iterator[Path]:
    """Writable temp directory inside the repo (sandbox-safe, git-ignored)."""

    path = ROOT / "var" / "pytest-tmp" / uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
