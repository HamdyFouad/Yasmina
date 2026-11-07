"""Prometheus metrics and the ASGI middleware that feeds them.

Metrics are deliberately low-cardinality: HTTP labels use the matched route
template (``/api/v1/invoices/{invoice_id}``) rather than the raw path, and
unmatched requests are folded into ``unmatched``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Any

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

__all__ = [
    "MetricsMiddleware",
    "render_metrics",
    "reset_metrics",
    "observe_http_request",
    "observe_workflow",
    "WORKFLOW_STATUSES",
]

_HTTP_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
_WORKFLOW_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 300.0)

WORKFLOW_STATUSES = frozenset({"pending_approval", "approved", "rejected", "error", "other"})

HTTP_REQUESTS = Counter(
    "yasmina_http_requests_total",
    "HTTP requests handled by the API.",
    ["method", "route", "status"],
)
HTTP_ERRORS = Counter(
    "yasmina_http_errors_total",
    "HTTP requests that returned a 4xx/5xx status.",
    ["method", "route", "status"],
)
HTTP_DURATION = Histogram(
    "yasmina_http_request_duration_seconds",
    "HTTP request latency in seconds.",
    ["method", "route"],
    buckets=_HTTP_BUCKETS,
)
WORKFLOW_RUNS = Counter(
    "yasmina_workflow_runs_total",
    "LangGraph workflow executions by terminal status.",
    ["status"],
)
WORKFLOW_DURATION = Histogram(
    "yasmina_workflow_duration_seconds",
    "LangGraph workflow duration in seconds.",
    ["status"],
    buckets=_WORKFLOW_BUCKETS,
)
WORKFLOW_ERRORS = Counter(
    "yasmina_workflow_errors_total",
    "LangGraph workflow executions that raised an exception.",
)


def observe_http_request(method: str, route: str, status: int, duration: float) -> None:
    HTTP_REQUESTS.labels(method=method, route=route, status=str(status)).inc()
    HTTP_DURATION.labels(method=method, route=route).observe(max(0.0, duration))
    if status >= 400:
        HTTP_ERRORS.labels(method=method, route=route, status=str(status)).inc()


def _workflow_label(status: str | None) -> str:
    normalized = (status or "other").strip().lower()
    return normalized if normalized in WORKFLOW_STATUSES else "other"


def observe_workflow(status: str | None, duration: float) -> None:
    label = _workflow_label(status)
    WORKFLOW_RUNS.labels(status=label).inc()
    WORKFLOW_DURATION.labels(status=label).observe(max(0.0, duration))
    if label == "error":
        WORKFLOW_ERRORS.inc()


def render_metrics() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST


def reset_metrics() -> None:
    """Clear metric state (test helper; never called by the application)."""

    for metric in (
        HTTP_REQUESTS,
        HTTP_ERRORS,
        HTTP_DURATION,
        WORKFLOW_RUNS,
        WORKFLOW_DURATION,
        WORKFLOW_ERRORS,
    ):
        metric.clear()


def _route_template(scope: dict[str, Any]) -> str:
    route = scope.get("route")
    template = getattr(route, "path", None)
    if isinstance(template, str) and template:
        return template
    return "unmatched"


class MetricsMiddleware:
    """ASGI middleware recording status, latency and error counts per route."""

    def __init__(self, app: Callable[..., Awaitable[None]], *, enabled: bool = True) -> None:
        self.app = app
        self.enabled = enabled

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or scope.get("path") == "/metrics":
            await self.app(scope, receive, send)
            return

        start = perf_counter()
        status_code = 500

        async def send_wrapper(message: dict[str, Any]) -> None:
            nonlocal status_code
            if message.get("type") == "http.response.start":
                status_code = int(message.get("status", 500))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            status_code = 500
            raise
        finally:
            if self.enabled:
                method = str(scope.get("method", "GET")).upper()
                observe_http_request(method, _route_template(scope), status_code, perf_counter() - start)
