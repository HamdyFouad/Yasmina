"""Rate limiting for the public API perimeter.

The default in-process limiter is a token bucket keyed by scope/client. When
``REDIS_URL`` is configured (production), the same bucket is evaluated atomically
inside Redis so every API replica shares one budget for a client.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from src.api.client_ip import resolve_client_ip
from src.config import Settings

__all__ = [
    "RateLimitDecision",
    "RateLimitMiddleware",
    "RateLimiter",
    "RedisTokenBucketRateLimiter",
    "TokenBucketRateLimiter",
    "build_rate_limiter",
]

logger = logging.getLogger("yasmina.rate_limit")

_SKIP_PATHS = frozenset({"/healthz", "/readyz", "/metrics", "/docs", "/redoc", "/openapi.json"})

REDIS_TOKEN_BUCKET_LUA = """
local capacity = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local tokens = tonumber(redis.call('HGET', KEYS[1], 'tokens'))
local updated = tonumber(redis.call('HGET', KEYS[1], 'updated_at'))
if tokens == nil or updated == nil then
  tokens = capacity
  updated = now
else
  local refill = math.max(0, now - updated) * (capacity / window)
  tokens = math.min(capacity, tokens + refill)
end
local allowed = 0
local retry_ms = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
else
  retry_ms = math.ceil((1 - tokens) * (window / capacity) * 1000)
end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'updated_at', now)
redis.call('PEXPIRE', KEYS[1], math.ceil(window * 1000))
return {allowed, math.floor(tokens), retry_ms}
"""


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


class RateLimiter(Protocol):
    async def acheck(self, key: str, *, limit: int, window_seconds: float) -> RateLimitDecision: ...


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

    async def acheck(self, key: str, *, limit: int, window_seconds: float) -> RateLimitDecision:
        return self.check(key, limit=limit, window_seconds=window_seconds)

    def prune(self) -> None:
        now = self._clock()
        stale = [
            key
            for key, bucket in self._buckets.items()
            if bucket.tokens >= bucket.capacity and now - bucket.updated_at > bucket.window
        ]
        for key in stale:
            self._buckets.pop(key, None)


class RedisTokenBucketRateLimiter:
    """Token bucket shared across processes via an atomic Redis Lua script."""

    def __init__(
        self,
        client: Any,
        *,
        clock: Callable[[], float] | None = None,
        key_prefix: str = "yasmina:ratelimit:",
        fail_open: bool = True,
    ) -> None:
        self._client = client
        self._clock = clock or time.time
        self._key_prefix = key_prefix
        self._fail_open = fail_open
        self.last_error: str | None = None

    async def acheck(self, key: str, *, limit: int, window_seconds: float) -> RateLimitDecision:
        now = self._clock()
        try:
            allowed, remaining, retry_ms = await self._client.eval(
                REDIS_TOKEN_BUCKET_LUA,
                1,
                f"{self._key_prefix}{key}",
                int(limit),
                max(0.001, float(window_seconds)),
                now,
            )
        except Exception as exc:  # pragma: no cover - exercised via fake client
            self.last_error = str(exc)
            logger.warning(
                "redis rate limiter unavailable; %s",
                "allowing request (fail-open)" if self._fail_open else "denying request (fail-closed)",
                extra={"event": "rate_limit_redis_error", "error": str(exc)},
            )
            if self._fail_open:
                return RateLimitDecision(allowed=True, remaining=max(0, limit), retry_after=0.0)
            return RateLimitDecision(allowed=False, remaining=0, retry_after=1.0)
        self.last_error = None
        return RateLimitDecision(
            allowed=bool(int(allowed)),
            remaining=max(0, int(remaining)),
            retry_after=max(0.0, float(retry_ms) / 1000.0),
        )

    async def aclose(self) -> None:
        close = getattr(self._client, "aclose", None)
        if close is not None:
            await close()


def build_rate_limiter(settings: Settings) -> RateLimiter:
    backend = settings.rate_limit_backend
    if backend == "auto":
        backend = "redis" if settings.redis_url else "memory"
    if backend == "redis":
        if not settings.redis_url:
            raise RuntimeError("RATE_LIMIT_BACKEND=redis requires REDIS_URL")
        from redis.asyncio import from_url

        client = from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_timeout=settings.redis_timeout_seconds,
            socket_connect_timeout=settings.redis_timeout_seconds,
        )
        return RedisTokenBucketRateLimiter(
            client,
            fail_open=settings.rate_limit_fail_open,
        )
    return TokenBucketRateLimiter()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Apply endpoint-specific token buckets before FastAPI dependencies."""

    def __init__(
        self,
        app: Any,
        *,
        settings: Settings,
        limiter: RateLimiter | None = None,
    ) -> None:
        super().__init__(app)
        self.settings = settings
        self._limiter = limiter

    @property
    def limiter(self) -> RateLimiter:
        """Build the backend lazily so Redis clients bind to the app event loop."""

        if self._limiter is None:
            self._limiter = build_rate_limiter(self.settings)
        return self._limiter

    def _scope(self, path: str) -> tuple[str, int] | None:
        if not self.settings.rate_limit_enabled or path in _SKIP_PATHS:
            return None
        if path.startswith("/api/v1/auth"):
            return "auth", self.settings.rate_limit_auth_per_minute
        if path.startswith("/api/v1/tenders/upload"):
            return "upload", self.settings.rate_limit_upload_per_minute
        if path.startswith("/api/v1"):
            return "business", self.settings.rate_limit_business_per_minute
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
        limiter = self.limiter
        request.app.state.rate_limiter = limiter
        client = resolve_client_ip(
            peer=request.client.host if request.client is not None else None,
            headers=request.headers,
            trust_proxy_headers=self.settings.trust_proxy_headers,
        )
        decision = await limiter.acheck(
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
