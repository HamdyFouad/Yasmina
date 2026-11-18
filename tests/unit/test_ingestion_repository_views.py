"""Unit guards for ingestion repository response mapping."""

from __future__ import annotations

from uuid import uuid4

from src.repositories.ingestion_repository import _job_view


class _ExpiredRow:
    """Mimic a SQLAlchemy row whose server/onupdate columns were expired."""

    def __init__(self) -> None:
        self.__dict__.update(
            id=uuid4(),
            tender_id=uuid4(),
            portal_name="bayern",
            status="failed",
            attempts=1,
            max_attempts=5,
            attachments=[],
            raw_object=None,
        )

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"_job_view read an expired column: {name}")


def test_job_view_uses_only_already_loaded_columns() -> None:
    view = _job_view(_ExpiredRow())  # type: ignore[arg-type]

    assert view.status == "failed"
    assert view.available_at is None
    assert view.locked_at is None
    assert view.last_error is None
    assert view.created_at is None
    assert view.updated_at is None
    assert view.quarantined is False
    assert view.quarantine_reason is None
