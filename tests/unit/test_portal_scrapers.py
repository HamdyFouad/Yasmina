"""Portal HTML extraction, CPV filtering and detail enrichment tests."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from src.config import Settings
from src.models.enums import ProcurementProcedure, TenderSourceFormat, TenderStatus
from src.scrapers.bayern_vergabe import BayernVergabeScraper
from src.scrapers.models import (
    filter_hygiene_cpv,
    is_hygiene_cpv,
    parse_portal_datetime,
)
from src.scrapers.oeffentlichevergabe import OeffentlicheVergabeScraper

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "portals"


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    async def put_bytes(self, object_name: str, data: bytes, content_type: str) -> str:
        self.objects[object_name] = (data, content_type)
        return object_name


def make_settings(**overrides: Any) -> Settings:
    data: dict[str, Any] = {
        "_env_file": None,
        "database_url": None,
        "scraper_request_delay_seconds": 0,
        "scraper_retry_backoff_seconds": 0,
        "scraper_user_agents": [],
        "scraper_harvest_attachments": False,
    }
    data.update(overrides)
    return Settings(**data)


def make_transport(routes: dict[str, str]) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path.endswith("/robots.txt"):
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        for needle, body in routes.items():
            if needle in str(request.url):
                return httpx.Response(200, text=body, headers={"Content-Type": "text/html; charset=utf-8"})
        return httpx.Response(404, text="missing fixture")

    return httpx.MockTransport(handler), requests


def test_cpv_filter_is_exact_and_accepts_missing_check_digit() -> None:
    assert is_hygiene_cpv("33760000-5")
    assert is_hygiene_cpv("33760000")
    assert not is_hygiene_cpv("33760001-5")
    assert not is_hygiene_cpv("45442100-8")
    assert filter_hygiene_cpv(("33760000-5", "39830000-9", "45442100-8", "33760000-5")) == (
        "33760000-5",
        "39830000-9",
    )


def test_german_and_english_deadline_formats() -> None:
    german = parse_portal_datetime("Abgabefrist: 31.03.2026 12:00 Uhr")
    english = parse_portal_datetime("Expiration time Nov 24, 2026, 9:00 AM")
    iso = parse_portal_datetime("2026-04-20 09:30")

    assert german is not None and german.isoformat().startswith("2026-03-31T12:00")
    assert english is not None and english.isoformat().startswith("2026-11-24T09:00")
    assert iso is not None and iso.isoformat().startswith("2026-04-20T09:30")


async def test_bayern_scraper_extracts_matches_and_enriches_detail() -> None:
    listing = (FIXTURES / "bayern_listing.html").read_text(encoding="utf-8")
    detail = (FIXTURES / "bayern_detail.html").read_text(encoding="utf-8")
    transport, requests = make_transport(
        {
            "2026-003": detail,
            "auftragsbekanntmachungen": listing,
        }
    )
    storage = FakeStorage()
    async with httpx.AsyncClient(transport=transport) as http:
        scraper = BayernVergabeScraper(
            make_settings(),
            storage,
            client=http,
            user_agents=["YasminaTest/1.0"],
            start_url="https://portal.example/auftragsbekanntmachungen",
        )
        discovered = await scraper.discover(max_pages=1)

    assert [item.portal_id for item in discovered] == ["2026-001", "2026-003"]
    assert discovered[0].cpv_codes == ("33760000-5", "39830000-9")
    assert discovered[1].cpv_codes == ("19520000-7",)
    assert scraper.last_report is not None
    assert scraper.last_report.skipped_cpv == 1
    assert scraper.last_report.pages_fetched == 1
    assert any("2026-003" in str(request.url) for request in requests)
    assert len(storage.objects) == 2  # listing + detail page

    tender = discovered[0].to_tender()
    assert tender.source_format is TenderSourceFormat.PORTAL
    assert tender.status is TenderStatus.PUBLISHED
    assert tender.tender_number == "BY-2026-001"
    assert tender.source_reference == "https://portal.example/veroeffentlichungen/auftragsbekanntmachungen/ausschreibung/2026-001"
    assert tender.metadata["portal"] == "vergabe.bayern"


async def test_federal_scraper_keeps_only_hygiene_cpvs() -> None:
    listing = (FIXTURES / "oeffentlichevergabe_listing.html").read_text(encoding="utf-8")
    transport, _ = make_transport({"search": listing})
    storage = FakeStorage()
    async with httpx.AsyncClient(transport=transport) as http:
        scraper = OeffentlicheVergabeScraper(
            make_settings(),
            storage,
            client=http,
            user_agents=["YasminaTest/1.0"],
            start_url="https://oeffentlichevergabe.de/ui/search",
        )
        discovered = await scraper.discover(max_pages=1)

    assert [item.portal_id for item in discovered] == ["notice-1001", "notice-1003"]
    assert discovered[1].procedure is ProcurementProcedure.OPEN_PROCEDURE
    assert discovered[1].publication_date is None
    assert scraper.last_report is not None
    assert scraper.last_report.skipped_cpv == 1
    assert len(storage.objects) == 1  # complete listing cards need no detail fetch


async def test_federal_scraper_returns_nothing_without_hygiene_cpv() -> None:
    listing = (FIXTURES / "oeffentlichevergabe_no_hygiene.html").read_text(encoding="utf-8")
    transport, _ = make_transport({"search": listing})
    async with httpx.AsyncClient(transport=transport) as http:
        scraper = OeffentlicheVergabeScraper(
            make_settings(),
            FakeStorage(),
            client=http,
            user_agents=["YasminaTest/1.0"],
            start_url="https://oeffentlichevergabe.de/ui/search",
        )
        discovered = await scraper.discover(max_pages=1)

    assert discovered == []
    assert scraper.last_report is not None
    assert scraper.last_report.skipped_cpv == 1
    assert scraper.last_report.skipped_invalid == 0


async def test_incremental_scrape_skips_old_publication_dates() -> None:
    listing = (FIXTURES / "bayern_listing.html").read_text(encoding="utf-8")
    detail = (FIXTURES / "bayern_detail.html").read_text(encoding="utf-8")
    transport, _ = make_transport(
        {
            "2026-003": detail,
            "auftragsbekanntmachungen": listing,
        }
    )
    async with httpx.AsyncClient(transport=transport) as http:
        scraper = BayernVergabeScraper(
            make_settings(),
            FakeStorage(),
            client=http,
            user_agents=["YasminaTest/1.0"],
            start_url="https://portal.example/auftragsbekanntmachungen",
        )
        discovered = await scraper.discover(
            max_pages=1,
            since=datetime(2026, 1, 16, tzinfo=timezone.utc),
        )

    assert [item.portal_id for item in discovered] == ["2026-003"]
    assert scraper.last_report is not None
    assert scraper.last_report.skipped_stale == 1


async def test_bayern_scraper_harvests_pdf_and_gaeb_attachments() -> None:
    listing = (
        "<html><body><article class='tender-card'>"
        "<h2><a href='/detail/2026-att'>Reinigung von Verwaltungsgebäuden</a></h2>"
        "<p class='client'>Auftraggeber: Stadt München</p>"
        "<p class='deadline'>Abgabefrist: 31.03.2026 12:00 Uhr</p>"
        "<p class='cpv'>CPV: 33760000-5</p>"
        "</article></body></html>"
    )
    detail = (FIXTURES / "bayern_detail_with_attachments.html").read_text(encoding="utf-8")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path.endswith("/robots.txt"):
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if path.endswith(".pdf"):
            return httpx.Response(200, content=b"%PDF-1.7 attachment", headers={"Content-Type": "application/pdf"})
        if path.endswith(".x83"):
            return httpx.Response(200, content=b"<GAEB>attachment</GAEB>", headers={"Content-Type": "application/xml"})
        if path.endswith("/2026-att"):
            return httpx.Response(200, text=detail, headers={"Content-Type": "text/html"})
        return httpx.Response(200, text=listing, headers={"Content-Type": "text/html"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        scraper = BayernVergabeScraper(
            make_settings(scraper_harvest_attachments=True),
            FakeStorage(),
            client=http,
            user_agents=["YasminaTest/1.0"],
            start_url="https://portal.example/bayern-list",
        )
        discovered = await scraper.discover(max_pages=1)

    assert len(discovered) == 1
    attachments = discovered[0].attachments
    assert [attachment.kind for attachment in attachments] == ["pdf", "gaeb"]
    assert attachments[0].filename == "leistungsverzeichnis.pdf"
    assert attachments[1].filename == "leistungsverzeichnis.x83"
    assert all(attachment.object_name.startswith("scrapers/vergabe.bayern/attachments/") for attachment in attachments)
    tender = discovered[0].to_tender()
    assert tender.metadata["attachment_objects"] == [
        attachment.object_name for attachment in attachments
    ]
    assert sum(1 for request in requests if request.url.path.endswith((".pdf", ".x83"))) == 2


async def test_bayern_scraper_follows_publication_iframe() -> None:
    iframe = (FIXTURES / "bayern_listing.html").read_text(encoding="utf-8")
    shell = (
        "<html><body><main><h1>Auftragsbekanntmachungen</h1>"
        '<iframe src="https://portal.example/public/publicationsFrame?filter=1"></iframe>'
        "</main></body></html>"
    )
    detail = (FIXTURES / "bayern_detail.html").read_text(encoding="utf-8")
    transport, requests = make_transport(
        {
            "2026-003": detail,
            "publicationsFrame": iframe,
            "index.html": shell,
        }
    )
    async with httpx.AsyncClient(transport=transport) as http:
        scraper = BayernVergabeScraper(
            make_settings(),
            FakeStorage(),
            client=http,
            user_agents=["YasminaTest/1.0"],
            start_url="https://portal.example/index.html",
        )
        discovered = await scraper.discover(max_pages=1)

    assert [item.portal_id for item in discovered] == ["2026-001", "2026-003"]
    assert any("publicationsFrame" in str(request.url) for request in requests)
