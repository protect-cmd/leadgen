from scrapers.base_scraper import BaseScraper
from models.filing import Filing


class SanDiegoScraper(BaseScraper):
    async def scrape(self) -> list[Filing]:
        raise NotImplementedError(
            "San Diego Superior Court portal selectors not yet mapped. "
            "See docs/portal_notes.md for discovery checklist."
        )
