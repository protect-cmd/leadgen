"""
Galveston County TX JP Courts eviction scraper.

Portal: https://portal.galvestoncountytx.gov/Portal/
Type: Tyler new Odyssey Portal (SPA) - distinct from legacy PublicAccess.
Coverage: JP Pcts 1-4 (Rikard, Apffel, Williams, McCumber).
Geo: Requires US IP (Railway US deployment).
Anti-bot: reCAPTCHA v2 on Search Hearings page.
"""

import time

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

    def navigate_to_search_hearings(self, page):
        """Load Search Hearings page and wait for SPA to render the form."""
        page.goto(SEARCH_HEARINGS_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_load_state("networkidle", timeout=15000)
        # Form readiness probe: Search button visible
        page.wait_for_selector("button:has-text('Search')", timeout=10000)
        return page

    def check_captcha_present(self, page):
        """Return True if reCAPTCHA v2 iframe is on the page."""
        return page.query_selector("iframe[src*='recaptcha']") is not None

    def attempt_captcha(self, page, max_wait_seconds: int = 15):
        """
        Attempt to pass reCAPTCHA v2 via stealth silent check.

        Returns dict:
          {'status': 'passed'}      - checkbox auto-approved (best case)
          {'status': 'challenge'}   - image puzzle appeared; needs solver service
          {'status': 'no_captcha'}  - no captcha on page
          {'status': 'error', ...}  - unexpected failure

        TODO: Integrate 2Captcha/Anti-Captcha or similar paid solver service
        for the 'challenge' case. For now we surface status so the caller
        can decide whether to retry, fail, or escalate to manual review.
        """
        if not self.check_captcha_present(page):
            return {"status": "no_captcha"}

        try:
            anchor_frame = page.frame_locator("iframe[src*='recaptcha/api2/anchor']")
            checkbox = anchor_frame.locator("#recaptcha-anchor")
            checkbox.click()

            deadline = time.time() + max_wait_seconds
            while time.time() < deadline:
                try:
                    if checkbox.get_attribute("aria-checked") == "true":
                        return {"status": "passed"}
                except Exception:
                    pass

                challenge = page.query_selector("iframe[src*='recaptcha/api2/bframe']")
                if challenge and challenge.is_visible():
                    return {
                        "status": "challenge",
                        "note": (
                            "Silent check failed; image puzzle present. "
                            "Requires captcha solver service integration."
                        ),
                    }

                time.sleep(0.5)

            return {"status": "error", "error": "timeout waiting for captcha resolution"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def session_probe(self):
        """
        End-to-end session probe: launch, navigate, attempt captcha.
        Returns dict with status. Intended for Railway preview deploy testing.
        """
        with sync_playwright() as p:
            browser, context, page = self._launch(p)
            try:
                self.navigate_to_search_hearings(page)
                captcha_present = self.check_captcha_present(page)
                captcha_result = self.attempt_captcha(page) if captcha_present else None
                return {
                    "ok": True,
                    "url": page.url,
                    "title": page.title(),
                    "captcha_present": captcha_present,
                    "captcha_result": captcha_result,
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
    import sys
    scraper = GalvestonTXJPScraper()
    if len(sys.argv) > 1 and sys.argv[1] == "probe":
        # Full session probe - portal access required (will fail from non-US IPs)
        print("Running session probe against portal.galvestoncountytx.gov...")
        result = scraper.session_probe()
    else:
        # Default: harmless smoke test against example.com
        print("Smoke test against example.com...")
        result = scraper.smoke_test("https://example.com")
    print(result)