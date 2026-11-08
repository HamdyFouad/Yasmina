"""Open-data API/feed adapters and API-first factory tests."""

from __future__ import annotations

from datetime import UTC

import httpx

from src.config import Settings
from src.scrapers.api_feeds import (
    ApiFeedScraper,
    TEDApiScraper,
    XVergabeApiScraper,
    build_api_scraper,
)
from src.scrapers.portal_config import PortalApiConfig, PortalTarget


class FakeStorage:
    async def put_bytes(self, object_name: str, data: bytes, content_type: str) -> str:
        return object_name


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url=None,
        scraper_request_delay_seconds=0,
        scraper_user_agents=["YasminaTest/1.0"],
        scraper_harvest_attachments=False,
    )


def make_target(
    *,
    api_type: str = "ted",
    fmt: str = "json",
    mapping: dict[str, str] | None = None,
) -> PortalTarget:
    return PortalTarget(
        id="ted",
        name="TED",
        start_url="https://ted.europa.eu/en/",
        engine="standard",
        portal_name="ted",
        portal_code="TED",
        country="EU",
        enabled=True,
        api=PortalApiConfig(
            type=api_type,
            url="https://api.example.test/notices",
            format="json" if fmt == "json" else "xml",
            mapping=mapping or {},
        ),
    )


def json_transport(payload: str) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/robots.txt"):
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return httpx.Response(200, text=payload, headers={"Content-Type": "application/json"})

    return httpx.MockTransport(handler)


def test_api_factory_prefers_ted_adapter() -> None:
    target = make_target()
    scraper = build_api_scraper(target, make_settings())
    assert isinstance(scraper, TEDApiScraper)
    assert scraper.portal_name == "ted"
    assert scraper.portal_code == "TED"

    xvergabe = make_target(api_type="xvergabe", fmt="xml")
    xml_scraper = build_api_scraper(xvergabe, make_settings())
    assert isinstance(xml_scraper, XVergabeApiScraper)

    generic = make_target(api_type="feed", fmt="json")
    assert isinstance(build_api_scraper(generic, make_settings()), ApiFeedScraper)


async def test_ted_json_feed_extracts_hygiene_notices() -> None:
    payload = """
    {
      "notices": [
        {
          "publication-number": "123456-2026",
          "notice-title": {"eng": "Cleaning services for municipal buildings"},
          "buyer-name": {"eng": "Stadt München"},
          "deadline-date-lot": "2026-04-30",
          "classification-cpv": ["33760000-5", "39830000-9"],
          "notice-url": "https://ted.europa.eu/en/notice/-/detail/123456-2026"
        },
        {
          "publication-number": "999999-2026",
          "notice-title": {"eng": "Road construction"},
          "buyer-name": {"eng": "Stadt München"},
          "deadline-date-lot": "2026-04-30",
          "classification-cpv": ["45233120-6"]
        }
      ]
    }
    """
    target = make_target()
    async with httpx.AsyncClient(transport=json_transport(payload)) as http:
        scraper = build_api_scraper(
            target,
            make_settings(),
            storage=FakeStorage(),
            client=http,
        )
        discovered = await scraper.discover()

    assert len(discovered) == 1
    notice = discovered[0]
    assert notice.portal_code == "TED"
    assert notice.portal_id == "123456-2026"
    assert notice.cpv_codes == ("33760000-5", "39830000-9")
    assert notice.submission_deadline.astimezone(UTC).date().isoformat() == "2026-04-30"
    assert notice.detail_url.endswith("123456-2026")
    assert scraper.last_report is not None
    assert scraper.last_report.skipped_cpv == 1


async def test_xvergabe_xml_feed_extracts_notices() -> None:
    payload = """
    <notices>
      <notice>
        <id>OV-2026-77</id>
        <title>Papierhandtücher für Verwaltungsgebäude</title>
        <client>Stadt Beispiel</client>
        <deadline>31.03.2026 12:00</deadline>
        <cpv>33760000-5</cpv>
        <url>https://example.test/notice/OV-2026-77</url>
      </notice>
    </notices>
    """
    target = make_target(api_type="xvergabe", fmt="xml")
    transport = httpx.MockTransport(
        lambda request: (
            httpx.Response(200, text="User-agent: *\nAllow: /\n")
            if request.url.path.endswith("/robots.txt")
            else httpx.Response(200, text=payload, headers={"Content-Type": "application/xml"})
        )
    )
    async with httpx.AsyncClient(transport=transport) as http:
        scraper = build_api_scraper(
            target,
            make_settings(),
            storage=FakeStorage(),
            client=http,
        )
        discovered = await scraper.discover()

    assert len(discovered) == 1
    assert discovered[0].portal_id == "OV-2026-77"
    assert discovered[0].title.startswith("Papierhandtücher")
    assert discovered[0].submission_deadline.isoformat().startswith("2026-03-31T10:00")
    assert discovered[0].detail_url == "https://example.test/notice/OV-2026-77"
    assert discovered[0].publication_date is None


def test_api_factory_rejects_target_without_api() -> None:
    target = PortalTarget(
        id="plain",
        name="Plain",
        start_url="https://plain.example/",
        engine="standard",
        portal_name="plain",
        portal_code="PL",
    )
    try:
        build_api_scraper(target, make_settings())
    except ValueError as exc:
        assert "has no API configuration" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


def test_api_config_defaults_are_sane() -> None:
    target = make_target()
    assert target.api is not None
    assert target.api.fallback_to_html is True
    assert target.api.format == "json"
    assert target.start_url.startswith("https://")
