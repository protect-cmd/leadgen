"""
Galveston County TX JP Courts eviction scraper.

Portal: https://portal.galvestoncountytx.gov/Portal/
Type: Tyler new Odyssey Portal (SPA) - distinct from legacy PublicAccess.
Coverage: JP Pcts 1-4 (Rikard, Apffel, Williams, McCumber).
Geo: Requires US IP (Railway US deployment).
Anti-bot: reCAPTCHA v2 on Search Hearings page.
"""

from playwright.sync_api import sync_playwright
from playwright_stealth import stealth_sync

STATE = "TX"
COUNTY = "Galveston"
NOTICE_TYPE = "Eviction"
TIMEZONE = "America/Chicago"

PORTAL_URL = "https://portal.galvestoncountytx.gov/Portal/"
SEARCH_HEARINGS_URL = "https://portal.galvestoncountytx.gov/Portal/Home/Dashboard/26"

JP_JUDGES = [
    {"name": "Rikard, Gregory L.", "precinct": "JP1"},
    {"name": "Apffel, D. Blake", "precinct": "JP2"},
    {"name": "Williams, Billy A. Jr.", "precinct": "JP3"},
    {"name": "McCumber, Kathleen", "precinct": "JP4"},
]


class GalvestonTXJPScraper:
    """Tyler new Odyssey Portal scraper for Galveston JP1-4 evictions."""

    DEFAULT_USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/127.0.0.0 Safari/537.36"
    )

    def __init__(self):
        self.last_error = None

    def _launch(self, p):
        """Launch headless Chromium with stealth-applied context."""
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=self.DEFAULT_USER_AGENT,
            timezone_id=TIMEZONE,
            locale="en-US",
        )
        page = context.new_page()
        stealth_sync(page)
        return browser, context, page

    def smoke_test(self, url: str = "https://example.com"):
        """Verify browser launches and can navigate. Returns dict with status."""
        with sync_playwright() as p:
            browser, context, page = self._launch(p)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                return {
                    "ok": True,
                    "url": page.url,
                    "title": page.title(),
                }
            except Exception as e:
                return {
                    "ok": False,
                    "error": str(e),
                    "url": page.url,
                }
            finally:
                browser.close()


if __name__ == "__main__":
    scraper = GalvestonTXJPScraper()
    print("Smoke test against example.com (should succeed)...")
    result = scraper.smoke_test("https://example.com")
    print(result)