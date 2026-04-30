from scrapers.base_scraper import BaseScraper
from models.filing import Filing


class RiversideScraper(BaseScraper):
    async def scrape(self) -> list[Filing]:
        raise NotImplementedError(
            "Riverside Superior Court portal selectors not yet mapped. "
            "See docs/portal_notes.md for discovery checklist."
        )
