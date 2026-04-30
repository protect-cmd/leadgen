from scrapers.base_scraper import BaseScraper
from models.filing import Filing


class OrangeCountyScraper(BaseScraper):
    async def scrape(self) -> list[Filing]:
        raise NotImplementedError(
            "Orange County Superior Court portal selectors not yet mapped. "
            "See docs/portal_notes.md for discovery checklist."
        )
