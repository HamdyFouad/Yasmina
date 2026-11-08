"""Engine-based scrapers for the portal software families in the German market.

Most public procurement portals run one of a handful of products. Instead of
maintaining one class per municipality, the engine classes below encode the
common listing/detail HTML behaviour and take the portal URL from
``PortalTarget`` configuration.
"""

from __future__ import annotations

from typing import Any, ClassVar, cast

from src.config import Settings
from src.scrapers.base_scraper import BaseScraper, ObjectWriter
from src.scrapers.domain_limits import DomainRateLimiter
from src.scrapers.html_portal import HTMLPortalScraper
from src.scrapers.portal_config import PortalTarget

__all__ = [
    "ENGINE_REGISTRY",
    "CosinexEngineScraper",
    "EngineScraperError",
    "RibEngineScraper",
    "StandardEVergabeScraper",
    "build_engine_scraper",
    "engine_class_for",
]


class EngineScraperError(ValueError):
    """Raised when a target names an unknown engine."""


class CosinexEngineScraper(HTMLPortalScraper):
    """Cosinex portals: table/list cards with a separate detail page."""

    engine_name: ClassVar[str] = "cosinex"
    follow_iframe = False
    fetch_details = True
    card_selectors: ClassVar[tuple[str, ...]] = (
        "table tbody tr",
        ".vergabe",
        ".tender-card",
        ".search-result",
        ".result",
        "article",
    )
    detail_hints: ClassVar[tuple[str, ...]] = (
        "/vergabe/",
        "/bekanntmachung",
        "/ausschreibung",
        "/notice",
        "/details",
        "/detail/",
        "/publication/",
    )


class RibEngineScraper(HTMLPortalScraper):
    """RIB iTWO portals, including the iframe-based Bayern publication list."""

    engine_name: ClassVar[str] = "rib"
    follow_iframe = True
    fetch_details = True
    card_selectors: ClassVar[tuple[str, ...]] = (
        "article",
        ".tender",
        ".search-result",
        ".result",
        ".bekanntmachung",
        "table tbody tr",
    )
    detail_hints: ClassVar[tuple[str, ...]] = (
        "/publicationsframe/",
        "/bekanntmachung",
        "/ausschreibung",
        "/notice",
        "/notices/",
        "/tender/details",
        "/details",
        "/detail/",
        "/publication/",
    )


class StandardEVergabeScraper(HTMLPortalScraper):
    """Standard e-Vergabe/DTVP-style HTML listings used by federal portals."""

    engine_name: ClassVar[str] = "standard"
    follow_iframe = False
    fetch_details = True
    card_selectors: ClassVar[tuple[str, ...]] = (
        ".tender-card",
        ".search-result",
        ".result",
        ".list-item",
        "article",
        "table tbody tr",
    )
    detail_hints: ClassVar[tuple[str, ...]] = (
        "/notice",
        "/notices/",
        "/tender/details",
        "/details",
        "/detail/",
        "/publication/",
        "/bekanntmachung",
        "/ausschreibung",
    )


ENGINE_REGISTRY: dict[str, type[HTMLPortalScraper]] = {
    "cosinex": CosinexEngineScraper,
    "rib": RibEngineScraper,
    "standard": StandardEVergabeScraper,
    # The XVergabe/DTVP standard uses the same generic HTML listing contract.
    "xvergabe": StandardEVergabeScraper,
    "dtvp": StandardEVergabeScraper,
}


def engine_class_for(engine: str) -> type[HTMLPortalScraper]:
    try:
        return ENGINE_REGISTRY[engine.strip().lower()]
    except KeyError as exc:
        raise EngineScraperError(
            f"unknown portal engine {engine!r}; expected one of {sorted(ENGINE_REGISTRY)}"
        ) from exc


def build_engine_scraper(
    target: PortalTarget,
    settings: Settings,
    storage: ObjectWriter | None = None,
    *,
    rate_limiter: DomainRateLimiter | None = None,
    **scraper_kwargs: Any,
) -> BaseScraper:
    """Instantiate the configured engine with a portal-specific class identity."""

    engine_cls = engine_class_for(target.engine)
    portal_cls = type(
        f"{target.id.title().replace('-', '')}Scraper",
        (engine_cls,),
        {
            "portal_name": target.portal_name,
            "portal_code": target.portal_code,
            "start_url": target.start_url,
        },
    )
    return cast(
        BaseScraper,
        portal_cls(
            settings,
            storage,
            request_delay_seconds=target.request_delay_seconds,
            rate_limiter=rate_limiter,
            **scraper_kwargs,
        ),
    )
