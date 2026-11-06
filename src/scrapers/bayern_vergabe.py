"""Bavarian public procurement portal scraper (vergabe.bayern.de)."""

from __future__ import annotations

from typing import ClassVar

from src.scrapers.html_portal import HTMLPortalScraper

__all__ = ["BayernVergabeScraper"]


class BayernVergabeScraper(HTMLPortalScraper):
    """Scrape the Bavarian announcement portal's publication iframe.

    The public page embeds the RIB publication list in an iframe; the generic
    parser follows that iframe and then extracts the ordinary HTML items.
    """

    portal_name: ClassVar[str] = "vergabe.bayern"
    portal_code: ClassVar[str] = "BY"
    start_url: str = "https://www.vergabe.bayern.de/veroeffentlichungen/auftragsbekanntmachungen/index.html"
    follow_iframe: ClassVar[bool] = True
