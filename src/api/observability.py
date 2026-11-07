"""Request correlation and structured access logging.

The middleware assigns (or honours) an ``X-Request-ID``, binds it to a context
variable and emits one JSON log line per HTTP request with method, route
template, status, duration and client metadata.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from time import perf_counter
from typing import Any
from uuid import uuid4

from src.api.client_ip import resolve_client_ip

__all__ = ["RequestContextMiddleware", "request_id_var"]

logger = logging.getLogger("yasmina.request")

request_id_var: ContextVar[str | None] = ContextVar("yasmina_request_id", default=None)


def _route_template(scope: dict[str, Any]) -> str:
    route = scope.get("route")
    template = getattr(route, "path", None)
    if isinstance(template, str) and template:
        return template
    return "unmatched"


def _find_header(headers: Any, name: bytes) -> str | None:
    for key, value in headers or []:
        if key.lower() == name:
            return str(value, "latin-1")
    return None


class RequestContextMiddleware:
    """Assign a request id and log one structured access line per request."""

    def __init__(self, app: Any, *, trust_proxy_headers: bool = False) -> None:
        self.app = app
        self.trust_proxy_headers = trust_proxy_headers

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = scope.get("headers") or []
        incoming = _find_header(headers, b"x-request-id")
        request_id = (incoming or uuid4().hex).strip()[:64] or uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        token = request_id_var.set(request_id)
        start = perf_counter()
        status_code = 500
        error: BaseException | None = None
        client = resolve_client_ip(
            peer=(scope.get("client") or (None, 0))[0],
            headers={
                "x-forwarded-for": _find_header(headers, b"x-forwarded-for") or "",
                "x-real-ip": _find_header(headers, b"x-real-ip") or "",
            },
            trust_proxy_headers=self.trust_proxy_headers,
        )

        async def send_wrapper(message: dict[str, Any]) -> None:
            nonlocal status_code
            if message.get("type") == "http.response.start":
                status_code = int(message.get("status", 500))
                response_headers = list(message.get("headers") or [])
                response_headers.append((b"x-request-id", request_id.encode("latin-1")))
                message["headers"] = response_headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except BaseException as exc:  # re-raised after the access log is written
            error = exc
            status_code = 500
            raise
        finally:
            logger.log(
                logging.ERROR if error is not None else logging.INFO,
                "request failed" if error is not None else "request completed",
                exc_info=error,
                extra={
                    "event": "http_request",
                    "request_id": request_id,
                    "method": str(scope.get("method", "GET")).upper(),
                    "path": scope.get("path", ""),
                    "route": _route_template(scope),
                    "status": status_code,
                    "duration_ms": round((perf_counter() - start) * 1000, 3),
                    "client": client,
                    "user_agent": (_find_header(headers, b"user-agent") or "")[:200],
                },
            )
            request_id_var.reset(token)
