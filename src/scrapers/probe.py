"""Read-only pilot probe for catalogue portals.

``probe_portal`` executes the configured HTML engine for one portal and reports
what the selectors extracted. It is deliberately isolated from the ingestion
pipeline:

* raw-page archiving and attachment harvesting are disabled, and the scraper is
  handed a storage stub that raises if anything ever tries to write;
* no repository, session factory, outbox or ingestion job is constructed;
* only ``robots.txt``, the listing and (when needed) detail pages are fetched.

The report distinguishes *cards seen* from *tenders parsed* so an operator can
tell a dead URL / layout change (zero cards) from an extraction regression
(cards seen but dropped).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from src.config import Settings
from src.scrapers.base_scraper import BaseScraper
from src.scrapers.engines import build_engine_scraper
from src.scrapers.models import DiscoveryDiagnostics, DiscoveryIssue
from src.scrapers.portal_config import PortalTarget

__all__ = [
    "ProbePortal",
    "ProbeReport",
    "ReadOnlyStorage",
    "ReadOnlyStorageError",
    "ZERO_RESULT_WARNING",
    "format_probe_report",
    "probe_portal",
]

ZERO_RESULT_WARNING = (
    "Zero results: the engine completed but parsed 0 notices. This strongly "
    "indicates a dead start_url, an API-first change, or a heavily modified "
    "portal layout that no longer matches the configured selectors."
)


class ReadOnlyStorageError(RuntimeError):
    """Raised when a probe tries to archive bytes; probes never write."""


class ReadOnlyStorage:
    """Storage stub proving that a probe cannot write to MinIO."""

    async def put_bytes(self, object_name: str, data: bytes, content_type: str) -> str:
        raise ReadOnlyStorageError(
            f"portal probe is read-only; refusing to archive {object_name!r} ({len(data)} bytes)"
        )


@dataclass(frozen=True, slots=True)
class ProbePortal:
    """Identity of the probed catalogue entry."""

    id: str
    name: str
    engine: str
    start_url: str
    enabled: bool
    api_feed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "engine": self.engine,
            "start_url": self.start_url,
            "enabled": self.enabled,
            "api_feed": self.api_feed,
        }


@dataclass(frozen=True, slots=True)
class ProbeReport:
    """Structured outcome of one read-only portal probe."""

    portal: ProbePortal
    max_pages: int
    pages_fetched: int
    notices_discovered: int
    notices_parsed: int
    skipped_cpv: int
    skipped_invalid: int
    skipped_stale: int
    details_fetched: int
    errors: tuple[str, ...]
    issues: tuple[DiscoveryIssue, ...]
    issues_truncated: int
    warnings: tuple[str, ...]
    elapsed_seconds: float
    probed_at: datetime

    @property
    def zero_results(self) -> bool:
        return self.notices_parsed == 0

    @property
    def read_only(self) -> bool:
        return True

    def as_dict(self) -> dict[str, Any]:
        return {
            "read_only": self.read_only,
            "probed_at": self.probed_at.isoformat(),
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "portal": self.portal.as_dict(),
            "max_pages": self.max_pages,
            "pages_fetched": self.pages_fetched,
            "notices_discovered": self.notices_discovered,
            "notices_parsed": self.notices_parsed,
            "skipped": {
                "cpv": self.skipped_cpv,
                "invalid": self.skipped_invalid,
                "stale": self.skipped_stale,
            },
            "details_fetched": self.details_fetched,
            "errors": list(self.errors),
            "issues": [
                {"url": issue.url, "code": issue.code, "message": issue.message} for issue in self.issues
            ],
            "issues_truncated": self.issues_truncated,
            "warnings": list(self.warnings),
            "zero_results": self.zero_results,
        }


def _warnings(
    *,
    target: PortalTarget,
    notices_discovered: int,
    notices_parsed: int,
    skipped_cpv: int,
) -> tuple[str, ...]:
    warnings: list[str] = []
    if notices_parsed == 0:
        warnings.append(ZERO_RESULT_WARNING)
        if notices_discovered == 0:
            warnings.append(
                "No listing cards were recognised; verify the start_url, robots.txt and "
                "the card_selectors of the configured engine."
            )
        elif skipped_cpv > 0:
            warnings.append(
                "Cards were parsed but none passed the hygiene CPV filter; check whether "
                "the portal publishes CPV codes in the listing/detail pages."
            )
        else:
            warnings.append(
                "Cards were parsed but rejected as incomplete; inspect the extraction "
                "issues below (deadline, title, client or CPV selectors)."
            )
    if target.api is not None:
        warnings.append(
            "This portal configures an API feed that the worker prefers over HTML; the "
            "probe exercised the HTML engine only."
        )
    return tuple(warnings)


async def probe_portal(
    target: PortalTarget,
    settings: Settings,
    *,
    max_pages: int = 1,
    client: httpx.AsyncClient | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    clock: Callable[[], float] | None = None,
) -> ProbeReport:
    """Run one portal through the engine without persisting anything.

    The scraper runs with archival and attachment harvesting disabled and a
    :class:`ReadOnlyStorage` stub, so any accidental write raises instead of
    reaching MinIO. No database session or outbox job is created.
    """

    if max_pages < 1:
        raise ValueError("max_pages must be >= 1")

    probe_settings = settings.model_copy(
        update={
            "scraper_archive_raw": False,
            "scraper_harvest_attachments": False,
            "scraper_max_pages": max(1, max_pages),
        }
    )
    scraper: BaseScraper = build_engine_scraper(
        target,
        probe_settings,
        ReadOnlyStorage(),
        client=client,
        sleep=sleep,
        clock=clock,
    )

    started = time.monotonic()
    report = await scraper.scrape(max_pages=max_pages)
    elapsed = time.monotonic() - started

    diagnostics = getattr(scraper, "last_diagnostics", None)
    if not isinstance(diagnostics, DiscoveryDiagnostics):
        diagnostics = DiscoveryDiagnostics(cards_seen=report.count, details_fetched=0)

    return ProbeReport(
        portal=ProbePortal(
            id=target.id,
            name=target.name,
            engine=target.engine,
            start_url=target.start_url,
            enabled=target.enabled,
            api_feed=target.api is not None,
        ),
        max_pages=max_pages,
        pages_fetched=report.pages_fetched,
        notices_discovered=diagnostics.cards_seen,
        notices_parsed=report.count,
        skipped_cpv=report.skipped_cpv,
        skipped_invalid=report.skipped_invalid,
        skipped_stale=report.skipped_stale,
        details_fetched=diagnostics.details_fetched,
        errors=report.errors,
        issues=diagnostics.issues,
        issues_truncated=diagnostics.issues_truncated,
        warnings=_warnings(
            target=target,
            notices_discovered=diagnostics.cards_seen,
            notices_parsed=report.count,
            skipped_cpv=report.skipped_cpv,
        ),
        elapsed_seconds=elapsed,
        probed_at=datetime.now(UTC),
    )


def _group_issues(issues: tuple[DiscoveryIssue, ...]) -> list[tuple[str, int, str, list[str]]]:
    grouped: dict[str, list[DiscoveryIssue]] = {}
    for issue in issues:
        grouped.setdefault(issue.code, []).append(issue)
    rows: list[tuple[str, int, str, list[str]]] = []
    for code, entries in grouped.items():
        rows.append((code, len(entries), entries[0].message, [entry.url for entry in entries[:3]]))
    return sorted(rows, key=lambda row: (-row[1], row[0]))


def format_probe_report(report: ProbeReport) -> str:
    """Render the probe result as an operator-friendly console report."""

    portal = report.portal
    lines: list[str] = [
        f"Portal probe (read-only) - {portal.id} | {portal.name}",
        f"engine: {portal.engine} | enabled: {'yes' if portal.enabled else 'no'}",
        f"start_url: {portal.start_url}",
        (
            f"pages fetched: {report.pages_fetched}/{report.max_pages} | "
            f"elapsed: {report.elapsed_seconds:.2f}s | details fetched: {report.details_fetched}"
        ),
        (
            f"notices discovered: {report.notices_discovered} | parsed: {report.notices_parsed} | "
            f"skipped cpv={report.skipped_cpv} invalid={report.skipped_invalid} "
            f"stale={report.skipped_stale}"
        ),
    ]

    if report.errors:
        lines.append(f"errors: {len(report.errors)}")
        lines.extend(f"  - {error}" for error in report.errors[:10])
        if len(report.errors) > 10:
            lines.append(f"  - ... {len(report.errors) - 10} more error(s)")

    grouped = _group_issues(report.issues)
    if grouped:
        suffix = f" (+{report.issues_truncated} truncated)" if report.issues_truncated else ""
        lines.append(f"extraction issues: {len(report.issues)}{suffix}")
        for code, count, message, examples in grouped:
            lines.append(f"  - {code} x {count}: {message}")
            for url in examples:
                lines.append(f"      {url}")

    for warning in report.warnings:
        lines.append(f"WARNING: {warning}")

    lines.append(
        "result: "
        + (
            "OK - notices parsed"
            if not report.zero_results
            else "ZERO RESULTS - nothing parsed (see warning above)"
        )
    )
    return "\n".join(lines)
