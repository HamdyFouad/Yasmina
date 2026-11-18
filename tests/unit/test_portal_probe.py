"""Portal probe tests: read-only dry run, diagnostics and CLI wiring."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from src import worker
from src.config import Settings
from src.scrapers.base_scraper import BaseScraper
from src.scrapers.models import DiscoveryIssue
from src.scrapers.portal_config import PortalApiConfig, PortalTarget
from src.scrapers.probe import (
    ZERO_POSITION_WARNING,
    ZERO_RESULT_WARNING,
    ProbePortal,
    ProbeReport,
    ReadOnlyStorage,
    ReadOnlyStorageError,
    count_position_rows,
    format_probe_report,
    probe_portal,
)

LISTING_HTML = """<!doctype html>
<html><body>
  <article>
    <h2>Toilettenpapier Rahmenvertrag</h2>
    <a href="/detail/1">Details</a>
    <p>Auftraggeber: Stadt Muster</p>
    <p>CPV: 33760000-5</p>
    <p>Abgabefrist: 15.01.2026 12:00</p>
  </article>
  <article>
    <h2>Nitrilhandschuhe Klinikum</h2>
    <a href="/detail/2">Details</a>
    <p>Auftraggeber: Klinikum Nord</p>
    <p>CPV: 33141420-0</p>
  </article>
</body></html>"""

DETAIL_WITHOUT_DEADLINE = """<!doctype html>
<html><body>
  <h1>Nitrilhandschuhe Klinikum</h1>
  <p>Auftraggeber: Klinikum Nord</p>
  <p>CPV: 33141420-0</p>
</body></html>"""

DETAIL_WITH_POSITIONS = """<!doctype html>
<html><body>
  <h1>Toilettenpapier Rahmenvertrag</h1>
  <table>
    <thead><tr><th>Position</th><th>Leistung</th><th>Menge</th><th>Einheit</th></tr></thead>
    <tbody>
      <tr><td>1</td><td>Toilettenpapier 3-lagig</td><td>1200</td><td>Karton</td></tr>
      <tr><td>2</td><td>Papierhandtücher</td><td>800</td><td>Karton</td></tr>
    </tbody>
  </table>
</body></html>"""

DETAIL_WITHOUT_POSITIONS = """<!doctype html>
<html><body>
  <h1>Toilettenpapier Rahmenvertrag</h1>
  <p>Die Leistungsbeschreibung befindet sich in der beigefügten GAEB-Datei.</p>
</body></html>"""

EMPTY_LISTING = """<!doctype html>
<html><body><main>Derzeit keine Veröffentlichungen.</main></body></html>"""


def make_settings(**overrides: Any) -> Settings:
    data: dict[str, Any] = {
        "_env_file": None,
        "database_url": None,
        "scraper_request_delay_seconds": 0,
        "scraper_retry_backoff_seconds": 0,
        "scraper_max_retries": 0,
        "scraper_archive_raw": True,
        "scraper_harvest_attachments": True,
        "scraper_user_agents": [],
    }
    data.update(overrides)
    return Settings(**data)


def make_target(**overrides: Any) -> PortalTarget:
    data: dict[str, Any] = {
        "id": "probe-portal",
        "name": "Probe Portal",
        "start_url": "https://portal.example/listing",
        "engine": "standard",
        "portal_name": "probe",
        "portal_code": "PX",
        "enabled": False,
    }
    data.update(overrides)
    return PortalTarget(**data)


def make_transport(
    *,
    listing: str = LISTING_HTML,
    detail_one: str = DETAIL_WITH_POSITIONS,
) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method != "GET":
            raise AssertionError(f"probe issued a non-GET request: {request.method} {request.url}")
        if request.url.path.endswith("/robots.txt"):
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if request.url.path == "/listing":
            return httpx.Response(200, text=listing, headers={"Content-Type": "text/html; charset=utf-8"})
        if request.url.path == "/detail/1":
            return httpx.Response(
                200,
                text=detail_one,
                headers={"Content-Type": "text/html; charset=utf-8"},
            )
        if request.url.path == "/detail/2":
            return httpx.Response(
                200,
                text=DETAIL_WITHOUT_DEADLINE,
                headers={"Content-Type": "text/html; charset=utf-8"},
            )
        return httpx.Response(404, text="not found")

    return httpx.MockTransport(handler), requests


async def test_probe_parses_first_page_and_reports_extraction_issues() -> None:
    transport, requests = make_transport()
    async with httpx.AsyncClient(transport=transport) as http:
        report = await probe_portal(make_target(), make_settings(), max_pages=1, client=http)

    assert report.read_only is True
    assert report.portal.id == "probe-portal"
    assert report.portal.enabled is False
    assert report.pages_fetched == 1
    assert report.max_pages == 1
    assert report.notices_discovered == 2
    assert report.notices_parsed == 1
    assert report.details_fetched == 1
    assert report.detail_pages_parsed == 1
    assert report.positions_parsed == 2
    assert report.notices_with_positions == 1
    assert report.zero_positions is False
    assert report.skipped_invalid == 1
    assert report.skipped_cpv == 0
    assert report.zero_results is False
    assert report.warnings == ()

    issue_codes = {issue.code for issue in report.issues}
    assert "missing_deadline" in issue_codes
    deadline_issue = next(issue for issue in report.issues if issue.code == "missing_deadline")
    assert deadline_issue.url.endswith("/detail/2")
    assert "deadline" in deadline_issue.message

    paths = [request.url.path for request in requests]
    assert paths[0] == "/robots.txt"
    assert "/listing" in paths
    assert "/detail/2" in paths
    assert "/detail/1" in paths
    # Archiving and attachment harvesting are forced off for the probe.
    assert all(request.method == "GET" for request in requests)

    payload = report.as_dict()
    assert payload["read_only"] is True
    assert payload["notices_discovered"] == 2
    assert payload["notices_parsed"] == 1
    assert payload["skipped"] == {"cpv": 0, "invalid": 1, "stale": 0}
    assert payload["positions_parsed"] == 2
    assert payload["notices_with_positions"] == 1
    assert payload["zero_positions"] is False
    assert payload["portal"] == {
        "id": "probe-portal",
        "name": "Probe Portal",
        "engine": "standard",
        "start_url": "https://portal.example/listing",
        "enabled": False,
        "api_feed": False,
    }
    assert payload["zero_results"] is False


async def test_probe_never_touches_object_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def guard(self: BaseScraper) -> Any:  # pragma: no cover - only reached on a write
        raise AssertionError("probe must not access object storage")

    monkeypatch.setattr(BaseScraper, "storage", property(guard))

    transport, _ = make_transport()
    async with httpx.AsyncClient(transport=transport) as http:
        report = await probe_portal(make_target(), make_settings(), client=http)

    assert report.notices_parsed == 1


def test_read_only_storage_refuses_writes() -> None:
    storage = ReadOnlyStorage()

    with pytest.raises(ReadOnlyStorageError, match="read-only"):
        asyncio.run(storage.put_bytes("probe/raw.html", b"<html/>", "text/html"))


def test_position_counter_requires_a_position_table() -> None:
    assert count_position_rows(DETAIL_WITH_POSITIONS) == 2
    assert count_position_rows("<table><tr><td>1</td><td>ignored</td></tr></table>") == 0


async def test_probe_warns_when_no_position_rows_are_visible() -> None:
    transport, _ = make_transport(detail_one=DETAIL_WITHOUT_POSITIONS)
    async with httpx.AsyncClient(transport=transport) as http:
        report = await probe_portal(make_target(), make_settings(), client=http)

    assert report.notices_parsed == 1
    assert report.positions_parsed == 0
    assert report.zero_positions is True
    assert ZERO_POSITION_WARNING in report.warnings
    assert ZERO_POSITION_WARNING in format_probe_report(report)

    empty_transport, _ = make_transport(listing=EMPTY_LISTING)
    async with httpx.AsyncClient(transport=empty_transport) as http:
        empty = await probe_portal(make_target(), make_settings(), client=http)
    assert empty.zero_results is True
    assert empty.positions_parsed == 0


async def test_probe_warns_about_zero_results() -> None:
    transport, _ = make_transport(listing=EMPTY_LISTING)
    async with httpx.AsyncClient(transport=transport) as http:
        report = await probe_portal(make_target(), make_settings(), client=http)

    assert report.notices_discovered == 0
    assert report.notices_parsed == 0
    assert report.zero_results is True
    assert report.warnings[0] == ZERO_RESULT_WARNING
    assert any("start_url" in warning for warning in report.warnings)
    assert any(issue.code == "no_cards" for issue in report.issues)

    text = format_probe_report(report)
    assert "notices discovered: 0 | parsed: 0" in text
    assert ZERO_RESULT_WARNING in text
    assert "ZERO RESULTS" in text


async def test_probe_mentions_api_feed_preference() -> None:
    target = make_target()
    transport, _ = make_transport()
    settings = make_settings()
    apified = PortalTarget(
        id=target.id,
        name=target.name,
        start_url=target.start_url,
        engine=target.engine,
        portal_name=target.portal_name,
        portal_code=target.portal_code,
        enabled=False,
        api=PortalApiConfig(type="feed", url="https://portal.example/api/notices"),
    )

    async with httpx.AsyncClient(transport=transport) as http:
        report = await probe_portal(apified, settings, client=http)

    assert report.portal.api_feed is True
    assert any("API feed" in warning for warning in report.warnings)


def test_probe_rejects_invalid_max_pages() -> None:
    with pytest.raises(ValueError, match="max_pages"):
        asyncio.run(probe_portal(make_target(), make_settings(), max_pages=0))


def make_report(*, zero: bool) -> ProbeReport:
    return ProbeReport(
        portal=ProbePortal(
            id="probe-portal",
            name="Probe Portal",
            engine="standard",
            start_url="https://portal.example/listing",
            enabled=False,
            api_feed=False,
        ),
        max_pages=1,
        pages_fetched=1,
        notices_discovered=0 if zero else 2,
        notices_parsed=0 if zero else 2,
        skipped_cpv=0,
        skipped_invalid=0,
        skipped_stale=0,
        details_fetched=0,
        errors=(),
        issues=(
            (DiscoveryIssue(url="https://portal.example/listing", code="no_cards", message="none"),)
            if zero
            else ()
        ),
        issues_truncated=0,
        warnings=(ZERO_RESULT_WARNING,) if zero else (),
        elapsed_seconds=0.5,
        probed_at=datetime(2026, 2, 1, 12, 0, tzinfo=UTC),
    )


def test_probe_parser_accepts_portal_and_flags() -> None:
    args = worker.build_parser().parse_args(["probe", "--portal", "nrw", "--max-pages", "2", "--json"])

    assert args.command == "probe"
    assert args.portal == "nrw"
    assert args.max_pages == 2
    assert args.as_json is True

    defaults = worker.build_parser().parse_args(["probe", "--portal", "nrw"])
    assert defaults.max_pages == 1
    assert defaults.as_json is False


def test_probe_cli_prints_json_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(worker, "get_settings", lambda: make_settings())
    seen: dict[str, Any] = {}

    async def fake_probe(target: PortalTarget, settings: Settings, *, max_pages: int = 1) -> ProbeReport:
        seen["target"] = target
        seen["max_pages"] = max_pages
        return make_report(zero=False)

    monkeypatch.setattr(worker, "probe_portal", fake_probe)

    assert worker.main(["probe", "--portal", "nrw", "--json"]) == 0

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["portal"]["id"] == "probe-portal"
    assert payload["notices_parsed"] == 2
    assert payload["read_only"] is True
    assert seen["target"].id == "nrw"
    assert seen["max_pages"] == 1


def test_probe_cli_exits_two_on_zero_results(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(worker, "get_settings", lambda: make_settings())

    async def fake_probe(target: PortalTarget, settings: Settings, *, max_pages: int = 1) -> ProbeReport:
        return make_report(zero=True)

    monkeypatch.setattr(worker, "probe_portal", fake_probe)

    assert worker.main(["probe", "--portal", "nrw", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["zero_results"] is True
    assert payload["warnings"][0] == ZERO_RESULT_WARNING


def test_probe_cli_prints_console_report(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(worker, "get_settings", lambda: make_settings())

    async def fake_probe(target: PortalTarget, settings: Settings, *, max_pages: int = 1) -> ProbeReport:
        return make_report(zero=True)

    monkeypatch.setattr(worker, "probe_portal", fake_probe)

    assert worker.main(["probe", "--portal", "nrw"]) == 2

    out = capsys.readouterr().out
    assert "Portal probe (read-only)" in out
    assert "ZERO RESULTS" in out


def test_probe_cli_rejects_unknown_portal(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(worker, "get_settings", lambda: make_settings())

    assert worker.main(["probe", "--portal", "does-not-exist"]) == 2

    err = capsys.readouterr().err
    assert "unknown portal" in err
    assert "nrw" in err
