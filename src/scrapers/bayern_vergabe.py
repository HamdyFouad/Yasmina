"""Bavarian public procurement portal scraper (vergabe.bayern.de)."""

from __future__ import annotations

from src.scrapers.engines import RibEngineScraper

__all__ = ["BayernVergabeScraper"]


class BayernVergabeScraper(RibEngineScraper):
    """Scrape the Bavarian announcement portal's publication iframe.

    The public page embeds the RIB publication list in an iframe; the engine
    follows that iframe and then extracts the ordinary HTML items.
    """

    portal_name = "vergabe.bayern"
    portal_code = "BY"
    start_url = "https://www.vergabe.bayern.de/veroeffentlichungen/auftragsbekanntmachungen/index.html"
