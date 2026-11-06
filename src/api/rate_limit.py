"""In-memory token-bucket rate limiting for the public API perimeter."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from src.config import Settings

__all__ = ["RateLimitDecision", "RateLimitMiddleware", "TokenBucketRateLimiter"]

_SKIP_PATHS = frozenset({"/healthz", "/readyz", "/metrics", "/docs", "/redoc", "/openapi.json"})


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    retry_after: float


@dataclass(slots=True)
class _Bucket:
    tokens: float
    updated_at: float
    capacity: int
    window: float


class TokenBucketRateLimiter:
    """Small asyncio-loop-local token bucket keyed by scope and client."""

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.monotonic
        self._buckets: dict[str, _Bucket] = {}

    def check(self, key: str, *, limit: int, window_seconds: float) -> RateLimitDecision:
        now = self._clock()
        window = max(0.001, float(window_seconds))
        bucket = self._buckets.get(key)
        if bucket is None or bucket.capacity != limit or bucket.window != window:
            bucket = _Bucket(tokens=float(limit), updated_at=now, capacity=limit, window=window)
            self._buckets[key] = bucket
        else:
            refill = (now - bucket.updated_at) * (limit / window)
            bucket.tokens = min(float(limit), bucket.tokens + max(0.0, refill))
            bucket.updated_at = now

        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return RateLimitDecision(
                allowed=True,
                remaining=max(0, int(bucket.tokens)),
                retry_after=0.0,
            )

        retry_after = (1.0 - bucket.tokens) / (limit / window)
        return RateLimitDecision(
            allowed=False,
            remaining=0,
            retry_after=max(0.01, retry_after),
        )

    def prune(self) -> None:
        now = self._clock()
        stale = [
            key
            for key, bucket in self._buckets.items()
            if bucket.tokens >= bucket.capacity and now - bucket.updated_at > bucket.window
        ]
        for key in stale:
            self._buckets.pop(key, None)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Apply endpoint-specific token buckets before FastAPI dependencies."""

    def __init__(self, app, *, settings: Settings) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self.settings = settings
        self.limiter = TokenBucketRateLimiter()

    def _scope(self, path: str) -> tuple[str, int] | None:
        if not self.settings.rate_limit_enabled or path in _SKIP_PATHS:
            return None
        if path.startswith("/api/v1/auth"):
            return "auth", self.settings.rate_limit_auth_per_minute
        if path.startswith("/api/v1/tenders/upload"):
            return "upload", self.settings.rate_limit_upload_per_minute
        if path.startswith("/api/v1"):
            return "api", self.settings.rate_limit_business_per_minute
        return None

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        scope_config = self._scope(request.url.path)
        if scope_config is None:
            return await call_next(request)
        scope, limit = scope_config
        client = request.client.host if request.client is not None else "unknown"
        decision = self.limiter.check(
            f"{scope}:{client}",
            limit=limit,
            window_seconds=60.0,
        )
        if not decision.allowed:
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "detail": "rate limit exceeded",
                    "scope": scope,
                    "retry_after": round(decision.retry_after, 2),
                },
                headers={
                    "Retry-After": str(max(1, int(decision.retry_after + 0.999))),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                },
            )
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
        return response
