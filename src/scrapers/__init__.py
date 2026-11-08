"""Compliant tender-portal scrapers and ingestion bridge."""

from __future__ import annotations

from src.scrapers.api_feeds import (
    ApiFeedScraper,
    TEDApiScraper,
    XVergabeApiScraper,
    build_api_scraper,
)
from src.scrapers.base_scraper import (
    DEFAULT_USER_AGENTS,
    BaseScraper,
    RobotsDeniedError,
    ScrapedPage,
    ScraperArchiveError,
    ScrapeReport,
    ScraperError,
    ScraperHTTPError,
    ScraperRobotsError,
)
from src.scrapers.bayern_vergabe import BayernVergabeScraper
from src.scrapers.domain_limits import (
    DomainRateLimiter,
    InMemoryDomainRateLimiter,
    RedisDomainRateLimiter,
    build_domain_rate_limiter,
)
from src.scrapers.engines import (
    ENGINE_REGISTRY,
    CosinexEngineScraper,
    RibEngineScraper,
    StandardEVergabeScraper,
    build_engine_scraper,
)
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
from src.scrapers.portal_config import (
    PortalApiConfig,
    PortalConfigError,
    PortalTarget,
    load_portal_targets,
    parse_portal_targets,
)
from src.services.tender_dedup import (
    TENDER_DEDUP_VERSION,
    normalize_dedup_text,
    tender_dedup_hash,
    tender_fingerprint,
)

__all__ = [
    "ApiFeedScraper",
    "BaseScraper",
    "BayernVergabeScraper",
    "CosinexEngineScraper",
    "DEFAULT_USER_AGENTS",
    "DiscoveredTender",
    "DomainRateLimiter",
    "ENGINE_REGISTRY",
    "HTMLPortalScraper",
    "HYGIENE_CPV_CODES",
    "InMemoryDomainRateLimiter",
    "IngestionPipeline",
    "OeffentlicheVergabeScraper",
    "PortalApiConfig",
    "PortalConfigError",
    "PortalIngestionBridge",
    "PortalIngestionError",
    "PortalIngestionResult",
    "PortalTarget",
    "RedisDomainRateLimiter",
    "RibEngineScraper",
    "RobotsDeniedError",
    "ScrapedPage",
    "ScrapeReport",
    "ScraperArchiveError",
    "ScraperError",
    "ScraperHTTPError",
    "ScraperRobotsError",
    "StandardEVergabeScraper",
    "TEDApiScraper",
    "TENDER_DEDUP_VERSION",
    "XVergabeApiScraper",
    "build_api_scraper",
    "build_domain_rate_limiter",
    "build_engine_scraper",
    "cpv_base",
    "extract_cpv_codes",
    "filter_hygiene_cpv",
    "is_hygiene_cpv",
    "load_portal_targets",
    "normalize_cpv",
    "normalize_dedup_text",
    "parse_portal_date",
    "parse_portal_datetime",
    "parse_portal_targets",
    "tender_dedup_hash",
    "tender_fingerprint",
]
