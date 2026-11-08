"""Federal German Bekanntmachungsservice scraper (oeffentlichevergabe.de)."""

from __future__ import annotations

from src.scrapers.engines import StandardEVergabeScraper

__all__ = ["OeffentlicheVergabeScraper"]


class OeffentlicheVergabeScraper(StandardEVergabeScraper):
    """Scrape the federal publication search results.

    The production UI is a JavaScript application, but its search-result cards
    are rendered from the same HTML patterns covered by the standard engine. A
    deployment can point ``start_url`` at its configured search export while
    retaining the same CPV and metadata extraction rules.
    """

    portal_name = "oeffentlichevergabe.de"
    portal_code = "OV"
    start_url = "https://oeffentlichevergabe.de/ui/search"
