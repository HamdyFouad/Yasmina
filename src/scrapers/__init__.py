"""Compliant tender-portal scrapers and ingestion bridge."""

from __future__ import annotations

from src.scrapers.base_scraper import (
    BaseScraper,
    DEFAULT_USER_AGENTS,
    RobotsDeniedError,
    ScrapedPage,
    ScrapeReport,
    ScraperArchiveError,
    ScraperError,
    ScraperHTTPError,
    ScraperRobotsError,
)
from src.scrapers.bayern_vergabe import BayernVergabeScraper
from src.scrapers.html_portal import HTMLPortalScraper
from src.scrapers.ingestion import (
    IngestionPipeline,
    PortalIngestionBridge,
    PortalIngestionError,
    PortalIngestionResult,
)
from src.scrapers.models import (
    HYGIENE_CPV_CODES,
    DiscoveredTender,
    cpv_base,
    extract_cpv_codes,
    filter_hygiene_cpv,
    is_hygiene_cpv,
    normalize_cpv,
    parse_portal_date,
    parse_portal_datetime,
)
from src.scrapers.oeffentlichevergabe import OeffentlicheVergabeScraper

__all__ = [
    "BaseScraper",
    "BayernVergabeScraper",
    "DEFAULT_USER_AGENTS",
    "DiscoveredTender",
    "HTMLPortalScraper",
    "HYGIENE_CPV_CODES",
    "IngestionPipeline",
    "OeffentlicheVergabeScraper",
    "PortalIngestionBridge",
    "PortalIngestionError",
    "PortalIngestionResult",
    "RobotsDeniedError",
    "ScrapedPage",
    "ScrapeReport",
    "ScraperArchiveError",
    "ScraperError",
    "ScraperHTTPError",
    "ScraperRobotsError",
    "cpv_base",
    "extract_cpv_codes",
    "filter_hygiene_cpv",
    "is_hygiene_cpv",
    "normalize_cpv",
    "parse_portal_date",
    "parse_portal_datetime",
]
