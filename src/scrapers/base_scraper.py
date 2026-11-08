"""Compliant HTTP/robots/rate-limit foundation for portal scrapers."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Protocol
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from src.config import Settings
from src.scrapers.domain_limits import DomainRateLimiter, InMemoryDomainRateLimiter
from src.scrapers.models import DiscoveredTender

__all__ = [
    "BaseScraper",
    "DEFAULT_USER_AGENTS",
    "ObjectWriter",
    "RobotsDeniedError",
    "ScrapedArtifact",
    "ScrapedPage",
    "ScrapeReport",
    "ScraperArchiveError",
    "ScraperError",
    "ScraperHTTPError",
    "ScraperRobotsError",
]

DEFAULT_USER_AGENTS: tuple[str, ...] = (
    "YasminaTenderBot/0.1 (procurement research; +https://github.com/HamdyFouad/Yasmina)",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0",
)

_RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
_HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml")


class ScraperError(RuntimeError):
    """Base error for a failed compliant portal fetch."""


class ScraperHTTPError(ScraperError):
    """Raised when a portal returns an unusable HTTP status."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ScraperRobotsError(ScraperError):
    """Raised when robots.txt cannot be checked safely."""


class RobotsDeniedError(ScraperRobotsError):
    """Raised when robots.txt disallows the requested URL."""


class ScraperArchiveError(ScraperError):
    """Raised when a raw page cannot be archived in object storage."""


class ObjectWriter(Protocol):
    async def put_bytes(self, object_name: str, data: bytes, content_type: str) -> str: ...


@dataclass(frozen=True, slots=True)
class ScrapedArtifact:
    url: str
    filename: str
    content_type: str
    content: bytes
    object_name: str
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class ScrapedPage:
    url: str
    final_url: str
    status_code: int
    text: str
    content: bytes
    content_type: str
    user_agent: str
    archived_object: str | None = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class ScrapeReport:
    portal: str
    discovered: tuple[DiscoveredTender, ...]
    pages_fetched: int
    skipped_cpv: int
    skipped_invalid: int
    errors: tuple[str, ...]
    skipped_stale: int = 0

    @property
    def count(self) -> int:
        return len(self.discovered)


@dataclass(slots=True)
class _RobotsEntry:
    parser: RobotFileParser
    expires_at: float
    crawl_delay: float | None


def _origin(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ScraperError(f"unsupported URL: {url!r}")
    return f"{parts.scheme}://{parts.netloc}".lower()


def _safe_component(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "._-" else "-" for char in value)
    return cleaned.strip("-")[:80] or "portal"


def _content_suffix(content_type: str) -> str:
    content_type = content_type.lower()
    if "json" in content_type:
        return ".json"
    if "pdf" in content_type:
        return ".pdf"
    if "xml" in content_type or "xhtml" in content_type:
        return ".xml"
    if "zip" in content_type:
        return ".zip"
    if any(value in content_type for value in _HTML_CONTENT_TYPES):
        return ".html"
    return ".bin"


class BaseScraper:
    """Polite async scraper with robots, throttling, UA rotation and archival.

    Compliance choices are intentionally conservative:

    * ``robots.txt`` is fetched before the first URL for every user agent and
      cached; HTTP 401/403/429 and 5xx responses fail closed.
    * The configured delay is raised to the robots ``Crawl-delay`` when larger.
    * Raw response bytes are archived before parsing when archival is enabled.
    """

    portal_name: ClassVar[str] = "portal"
    portal_code: ClassVar[str] = "PORTAL"

    def __init__(
        self,
        settings: Settings,
        storage: ObjectWriter | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        clock: Callable[[], float] | None = None,
        user_agents: Sequence[str] | None = None,
        request_delay_seconds: float | None = None,
        rate_limiter: DomainRateLimiter | None = None,
    ) -> None:
        self.settings = settings
        self._storage = storage
        self._client = client
        self._sleep = sleep or asyncio.sleep
        self._clock = clock or time.monotonic
        agents = tuple(user_agents or settings.scraper_user_agents or DEFAULT_USER_AGENTS)
        if not agents or any(not agent.strip() for agent in agents):
            raise ValueError("at least one non-empty User-Agent is required")
        self._user_agents = agents
        self._user_agent_index = 0
        self._robots: dict[tuple[str, str], _RobotsEntry] = {}
        self._rate_limiter = rate_limiter or InMemoryDomainRateLimiter(
            clock=self._clock,
            sleep=self._sleep,
        )
        self._request_delay = max(
            0.0,
            (
                request_delay_seconds
                if request_delay_seconds is not None
                else settings.scraper_request_delay_seconds
            ),
        )
        self.last_report: ScrapeReport | None = None

    @property
    def storage(self) -> ObjectWriter:
        if self._storage is None:
            from src.integrations.minio_client import MinioObjectStorage

            self._storage = MinioObjectStorage(self.settings)
        return self._storage

    def next_user_agent(self) -> str:
        """Rotate through the configured User-Agent pool."""

        agent = self._user_agents[self._user_agent_index % len(self._user_agents)]
        self._user_agent_index += 1
        return agent

    async def _throttle(self, url: str, crawl_delay: float | None) -> None:
        host = urlsplit(url).netloc.lower()
        delay = max(self._request_delay, crawl_delay or 0.0)
        await self._rate_limiter.wait(host, delay)

    async def _send(self, url: str, user_agent: str) -> httpx.Response:
        headers = {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.5",
            "Cache-Control": "no-cache",
        }
        if self._client is not None:
            return await self._client.get(url, headers=headers, follow_redirects=True)
        async with httpx.AsyncClient(
            timeout=self.settings.scraper_timeout_seconds,
            follow_redirects=True,
        ) as client:
            return await client.get(url, headers=headers)

    async def _get_response(
        self, url: str, user_agent: str, crawl_delay: float | None = None
    ) -> httpx.Response:
        last_error: str | None = None
        for attempt in range(self.settings.scraper_max_retries + 1):
            await self._throttle(url, crawl_delay)
            try:
                response = await self._send(url, user_agent)
            except httpx.HTTPError as exc:
                last_error = str(exc)
                if attempt >= self.settings.scraper_max_retries:
                    raise ScraperHTTPError(f"portal request failed: {last_error}") from exc
                await self._sleep(self.settings.scraper_retry_backoff_seconds * (2**attempt))
                continue

            if response.status_code in _RETRY_STATUSES and attempt < self.settings.scraper_max_retries:
                retry_after = response.headers.get("Retry-After", "").strip()
                delay = (
                    min(float(retry_after), 60.0)
                    if retry_after.isdigit()
                    else self.settings.scraper_retry_backoff_seconds * (2**attempt)
                )
                await self._sleep(delay)
                continue
            return response
        raise ScraperHTTPError(f"portal request failed after retries: {last_error or 'unknown error'}")

    async def _robots_for(self, url: str, user_agent: str) -> _RobotsEntry:
        origin = _origin(url)
        cache_key = (origin, user_agent)
        now = self._clock()
        cached = self._robots.get(cache_key)
        if cached is not None and cached.expires_at >= now:
            return cached

        robots_url = f"{origin}/robots.txt"
        try:
            response = await self._get_response(robots_url, user_agent)
        except ScraperHTTPError as exc:
            raise ScraperRobotsError(f"cannot verify robots.txt at {robots_url}: {exc}") from exc

        parser = RobotFileParser()
        parser.set_url(robots_url)
        if response.status_code in {401, 403, 429} or response.status_code >= 500:
            parser.parse(["User-agent: *", "Disallow: /"])
        elif 400 <= response.status_code < 500:
            # RFC 9309: 4xx (other than access-control errors) means no rules.
            parser.parse([])
        else:
            parser.parse(response.text.splitlines())

        crawl_delay = parser.crawl_delay(user_agent)
        entry = _RobotsEntry(
            parser=parser,
            expires_at=now + self.settings.scraper_robots_cache_seconds,
            crawl_delay=float(crawl_delay) if crawl_delay is not None else None,
        )
        self._robots[cache_key] = entry
        return entry

    async def _archive(self, page: ScrapedPage) -> str | None:
        digest = hashlib.sha256(page.content).hexdigest()
        prefix = self.settings.scraper_archive_prefix.strip("/") or "scrapers"
        day = page.fetched_at.astimezone(UTC).date().isoformat()
        object_name = (
            f"{prefix}/{_safe_component(self.portal_name)}/{day}/{digest}{_content_suffix(page.content_type)}"
        )
        try:
            return await self.storage.put_bytes(object_name, page.content, page.content_type or "text/html")
        except Exception as exc:
            raise ScraperArchiveError(f"cannot archive {page.url}: {exc}") from exc

    async def fetch(self, url: str, *, archive: bool | None = None) -> ScrapedPage:
        """Fetch one URL after robots/rate checks and archive the raw bytes."""

        user_agent = self.next_user_agent()
        robots = await self._robots_for(url, user_agent)
        if not robots.parser.can_fetch(user_agent, url):
            raise RobotsDeniedError(f"robots.txt disallows {url}")

        response = await self._get_response(url, user_agent, robots.crawl_delay)
        if not 200 <= response.status_code < 300:
            raise ScraperHTTPError(
                f"portal returned {response.status_code} for {url}",
                status_code=response.status_code,
            )
        final_url = str(response.url)
        if _origin(final_url) != _origin(url):
            final_robots = await self._robots_for(final_url, user_agent)
            if not final_robots.parser.can_fetch(user_agent, final_url):
                raise RobotsDeniedError(f"robots.txt disallows redirect target {final_url}")

        content_type = response.headers.get("Content-Type", "text/html").split(";", 1)[0].strip()
        page = ScrapedPage(
            url=url,
            final_url=str(response.url),
            status_code=response.status_code,
            text=response.text,
            content=response.content,
            content_type=content_type or "text/html",
            user_agent=user_agent,
        )
        should_archive = self.settings.scraper_archive_raw if archive is None else archive
        if should_archive:
            object_name = await self._archive(page)
            page = ScrapedPage(
                url=page.url,
                final_url=page.final_url,
                status_code=page.status_code,
                text=page.text,
                content=page.content,
                content_type=page.content_type,
                user_agent=page.user_agent,
                archived_object=object_name,
                fetched_at=page.fetched_at,
            )
        return page

    async def download_attachment(self, url: str, *, filename: str | None = None) -> ScrapedArtifact:
        """Download and archive a PDF/GAEB attachment with the same safeguards."""

        user_agent = self.next_user_agent()
        robots = await self._robots_for(url, user_agent)
        if not robots.parser.can_fetch(user_agent, url):
            raise RobotsDeniedError(f"robots.txt disallows {url}")

        response = await self._get_response(url, user_agent, robots.crawl_delay)
        if not 200 <= response.status_code < 300:
            raise ScraperHTTPError(
                f"portal returned {response.status_code} for attachment {url}",
                status_code=response.status_code,
            )
        content = response.content
        if not content:
            raise ScraperError(f"empty attachment response for {url}")
        if len(content) > self.settings.scraper_max_attachment_mb * 1024 * 1024:
            raise ScraperError(f"attachment {url} exceeds {self.settings.scraper_max_attachment_mb} MB")

        requested_name = Path(filename or urlsplit(url).path).name or "attachment"
        safe_name = "".join(char if char.isalnum() or char in "._-" else "_" for char in requested_name)
        safe_name = safe_name[:180] or "attachment"
        content_type = (
            response.headers.get("Content-Type", "application/octet-stream").split(";", 1)[0].strip()
        )
        suffix = Path(safe_name).suffix.lower()
        if not suffix:
            suffix = _content_suffix(content_type)
            safe_name = f"{safe_name}{suffix}"
        digest = hashlib.sha256(content).hexdigest()
        prefix = self.settings.scraper_archive_prefix.strip("/") or "scrapers"
        object_name = (
            f"{prefix}/{_safe_component(self.portal_name)}/attachments/"
            f"{digest}{Path(safe_name).suffix.lower()}"
        )
        try:
            await self.storage.put_bytes(
                object_name,
                content,
                content_type or "application/octet-stream",
            )
        except Exception as exc:
            raise ScraperArchiveError(f"cannot archive attachment {url}: {exc}") from exc
        return ScrapedArtifact(
            url=url,
            filename=safe_name,
            content_type=content_type or "application/octet-stream",
            content=content,
            object_name=object_name,
        )

    async def discover(
        self,
        *,
        max_pages: int | None = None,
        since: datetime | None = None,
    ) -> list[DiscoveredTender]:
        raise NotImplementedError

    async def scrape(
        self,
        *,
        max_pages: int | None = None,
        since: datetime | None = None,
    ) -> ScrapeReport:
        """Discover tenders and return a report with request/skip counters."""

        discovered = await self.discover(max_pages=max_pages, since=since)
        if self.last_report is not None:
            return self.last_report
        report = ScrapeReport(
            portal=self.portal_name,
            discovered=tuple(discovered),
            pages_fetched=0,
            skipped_cpv=0,
            skipped_invalid=0,
            errors=(),
        )
        self.last_report = report
        return report
