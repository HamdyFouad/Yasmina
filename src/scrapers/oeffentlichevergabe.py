"""Federal German Bekanntmachungsservice scraper (oeffentlichevergabe.de)."""

from __future__ import annotations

from typing import ClassVar

from src.scrapers.html_portal import HTMLPortalScraper

__all__ = ["OeffentlicheVergabeScraper"]


class OeffentlicheVergabeScraper(HTMLPortalScraper):
    """Scrape the federal publication search results.

    The production UI is a JavaScript application, but its search-result cards
    are rendered from the same HTML patterns covered by the generic parser. A
    deployment can point ``start_url`` at its configured search export while
    retaining the same CPV and metadata extraction rules.
    """

    portal_name: ClassVar[str] = "oeffentlichevergabe.de"
    portal_code: ClassVar[str] = "OV"
    start_url: ClassVar[str] = "https://oeffentlichevergabe.de/ui/search"
