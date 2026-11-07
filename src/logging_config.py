"""Structured JSON logging shared by the API, workers and CLIs.

Log-aggregator friendly output: one JSON object per line with a stable set of
top-level fields (``timestamp``, ``level``, ``logger``, ``message``) plus any
``extra=`` attributes the caller provides, for example request metadata.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

__all__ = ["JsonFormatter", "configure_logging"]

_STANDARD_ATTRS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)
_TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


class JsonFormatter(logging.Formatter):
    """Render a log record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _STANDARD_ATTRS or key.startswith("_") or key in payload:
                continue
            payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = record.stack_info
        return json.dumps(payload, ensure_ascii=False, default=str)


def _build_handler(json_format: bool) -> logging.Handler:
    # stderr keeps CLI stdout machine-readable (JSON command results).
    handler = logging.StreamHandler(sys.stderr)
    if json_format:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(_TEXT_FORMAT))
    return handler


def configure_logging(
    level: str = "INFO",
    *,
    json_format: bool = True,
    include_uvicorn: bool = True,
) -> None:
    """Install a single stdout handler on the root and uvicorn loggers.

    Safe to call more than once: the previous handler is replaced instead of
    stacking duplicates, which matters when the app is created by test clients.
    """

    numeric_level = getattr(logging, level.upper(), logging.INFO)
    handler = _build_handler(json_format)

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(numeric_level)

    if include_uvicorn:
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            logger = logging.getLogger(name)
            for existing in list(logger.handlers):
                logger.removeHandler(existing)
            logger.addHandler(handler)
            logger.propagate = False
            logger.setLevel(numeric_level)
