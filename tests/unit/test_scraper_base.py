"""Base scraper compliance tests: robots, rate limiting, UA rotation, archival."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from src.config import Settings
from src.scrapers.base_scraper import (
    BaseScraper,
    RobotsDeniedError,
    ScraperArchiveError,
    ScraperRobotsError,
)


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    async def put_bytes(self, object_name: str, data: bytes, content_type: str) -> str:
        self.objects[object_name] = (data, content_type)
        return object_name


class DummyScraper(BaseScraper):
    portal_name = "dummy.portal"
    portal_code = "D"


def make_settings(**overrides: Any) -> Settings:
    data: dict[str, Any] = {
        "_env_file": None,
        "database_url": None,
        "scraper_request_delay_seconds": 0,
        "scraper_retry_backoff_seconds": 0,
        "scraper_user_agents": [],
    }
    data.update(overrides)
    return Settings(**data)


def make_transport(
    *,
    robots_status: int = 200,
    robots_body: str = "User-agent: *\nAllow: /\n",
    page_status: int = 200,
    page_body: str = "<html><body>portal</body></html>",
) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/robots.txt"):
            return httpx.Response(robots_status, text=robots_body)
        return httpx.Response(page_status, text=page_body, headers={"Content-Type": "text/html; charset=utf-8"})

    return httpx.MockTransport(handler), requests


async def test_fetch_archives_raw_html_and_respects_robots() -> None:
    storage = FakeStorage()
    transport, requests = make_transport()
    async with httpx.AsyncClient(transport=transport) as http:
        scraper = DummyScraper(
            make_settings(),
            storage,
            client=http,
            user_agents=["YasminaTest/1.0"],
        )
        page = await scraper.fetch("https://portal.example/list")

    assert page.status_code == 200
    assert page.text == "<html><body>portal</body></html>"
    assert page.archived_object is not None
    assert page.archived_object.startswith("scrapers/dummy.portal/")
    assert page.archived_object.endswith(".html")
    assert storage.objects[page.archived_object][0] == b"<html><body>portal</body></html>"
    assert [request.url.path for request in requests] == ["/robots.txt", "/list"]


async def test_robots_404_allows_page_fetch() -> None:
    transport, requests = make_transport(robots_status=404, robots_body="not found")
    async with httpx.AsyncClient(transport=transport) as http:
        scraper = DummyScraper(
            make_settings(),
            FakeStorage(),
            client=http,
            user_agents=["YasminaTest/1.0"],
        )
        page = await scraper.fetch("https://portal.example/list")

    assert page.status_code == 200
    assert [request.url.path for request in requests] == ["/robots.txt", "/list"]


async def test_robots_crawl_delay_raises_the_request_interval() -> None:
    clock_value = 0.0
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        nonlocal clock_value
        sleeps.append(delay)
        clock_value += delay

    def clock() -> float:
        return clock_value

    transport, _ = make_transport(
        robots_body="User-agent: *\nAllow: /\nCrawl-delay: 7\n",
    )
    async with httpx.AsyncClient(transport=transport) as http:
        scraper = DummyScraper(
            make_settings(scraper_request_delay_seconds=0),
            FakeStorage(),
            client=http,
            sleep=sleep,
            clock=clock,
            user_agents=["YasminaTest/1.0"],
        )
        await scraper.fetch("https://portal.example/one")
        await scraper.fetch("https://portal.example/two")
        await scraper.fetch("https://portal.example/three")

    assert sleeps == [7, 7]


async def test_robots_disallow_blocks_the_page_request() -> None:
    storage = FakeStorage()
    transport, requests = make_transport(robots_body="User-agent: *\nDisallow: /private\n")
    async with httpx.AsyncClient(transport=transport) as http:
        scraper = DummyScraper(make_settings(), storage, client=http, user_agents=["YasminaTest/1.0"])
        with pytest.raises(RobotsDeniedError):
            await scraper.fetch("https://portal.example/private/list")

    assert [request.url.path for request in requests] == ["/robots.txt"]
    assert storage.objects == {}


async def test_robots_server_error_fails_closed() -> None:
    transport, _ = make_transport(robots_status=503)
    async with httpx.AsyncClient(transport=transport) as http:
        scraper = DummyScraper(
            make_settings(scraper_max_retries=0),
            FakeStorage(),
            client=http,
            user_agents=["YasminaTest/1.0"],
        )
        with pytest.raises(ScraperRobotsError):
            await scraper.fetch("https://portal.example/list")


async def test_rate_limit_and_user_agent_rotation() -> None:
    clock_value = 0.0
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        nonlocal clock_value
        sleeps.append(delay)
        clock_value += delay

    def clock() -> float:
        return clock_value

    transport, requests = make_transport()
    async with httpx.AsyncClient(transport=transport) as http:
        scraper = DummyScraper(
            make_settings(scraper_request_delay_seconds=5),
            FakeStorage(),
            client=http,
            sleep=sleep,
            clock=clock,
            user_agents=["Agent-A/1.0", "Agent-B/1.0"],
        )
        await scraper.fetch("https://portal.example/one")
        await scraper.fetch("https://portal.example/two")

    page_agents = [request.headers["User-Agent"] for request in requests if not request.url.path.endswith("/robots.txt")]
    assert page_agents == ["Agent-A/1.0", "Agent-B/1.0"]
    assert sleeps == [5, 5, 5]  # page A, robots B, page B; robots A was first


async def test_archive_failure_is_explicit() -> None:
    class BrokenStorage:
        async def put_bytes(self, object_name: str, data: bytes, content_type: str) -> str:
            raise OSError("storage offline")

    transport, _ = make_transport()
    async with httpx.AsyncClient(transport=transport) as http:
        scraper = DummyScraper(
            make_settings(),
            BrokenStorage(),
            client=http,
            user_agents=["YasminaTest/1.0"],
        )
        with pytest.raises(ScraperArchiveError):
            await scraper.fetch("https://portal.example/list")
