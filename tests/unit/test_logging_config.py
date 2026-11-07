"""Structured JSON logging formatter and handler configuration."""

from __future__ import annotations

import json
import logging

from src.logging_config import JsonFormatter, configure_logging


def _record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="yasmina.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_emits_one_object_with_extras() -> None:
    payload = json.loads(JsonFormatter().format(_record(request_id="abc123", status=200)))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "yasmina.test"
    assert payload["message"] == "hello world"
    assert payload["request_id"] == "abc123"
    assert payload["status"] == 200
    assert payload["timestamp"].endswith("+00:00")


def test_json_formatter_serialises_exceptions() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord(
            name="yasmina.test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=20,
            msg="failed",
            args=(),
            exc_info=sys.exc_info(),
        )

    payload = json.loads(JsonFormatter().format(record))

    assert payload["level"] == "ERROR"
    assert "ValueError: boom" in payload["exception"]


def test_configure_logging_replaces_handlers_idempotently() -> None:
    configure_logging("DEBUG", json_format=True)
    configure_logging("WARNING", json_format=False)

    root = logging.getLogger()
    assert root.level == logging.WARNING
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, logging.Formatter)
    assert not isinstance(root.handlers[0].formatter, JsonFormatter)
    assert logging.getLogger("uvicorn.access").handlers == root.handlers

    configure_logging("INFO", json_format=True)
    assert isinstance(logging.getLogger().handlers[0].formatter, JsonFormatter)


def test_configure_logging_can_leave_uvicorn_alone() -> None:
    configure_logging("INFO", json_format=True, include_uvicorn=False)

    assert logging.getLogger("uvicorn.access").handlers != logging.getLogger().handlers
