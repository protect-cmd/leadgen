"""
Fort Bend County TX JP Courts eviction scraper.

Portal: https://tylerpaw.fortbendcountytx.gov/PublicAccess/default.aspx
Type: Tyler legacy PublicAccess (same template as Denton/Montgomery).
Coverage: All JP precincts pooled under single 'Fort Bend' location entry.
Geo: Requires US IP (Railway US deployment) - .gov subdomain.
Anti-bot: Cloudflare on main county site; tylerpaw subdomain accessible.
Address recovery: Case detail page hides street; Original Petition PDF
exposes full address per TX Justice Court Rule 510.3 (extracted via
pdfplumber).
"""

from playwright.sync_api import sync_playwright

STATE = "TX"
COUNTY = "Fort Bend"
NOTICE_TYPE = "Eviction"
TIMEZONE = "America/Chicago"

PORTAL_BASE = "https://tylerpaw.fortbendcountytx.gov"
PORTAL_URL = f"{PORTAL_BASE}/PublicAccess/default.aspx"


class FortBendTXJPScraper:
    """Tyler legacy PublicAccess scraper for Fort Bend JP evictions."""

    DEFAULT_USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/127.0.0.0 Safari/537.36"
    )

    def __init__(self):
        self.last_error = None

    def _launch(self, p):
        """Launch headless Chromium with realistic browser context."""
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
        return browser, context, page

    def smoke_test(self, url: str = "https://example.com"):
        """Verify browser launches and can navigate. Returns dict with status."""
        with sync_playwright() as p:
            browser, context, page = self._launch(p)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                return {"ok": True, "url": page.url, "title": page.title()}
            except Exception as e:
                return {"ok": False, "error": str(e), "url": page.url}
            finally:
                browser.close()

    def navigate_to_civil_search(self, page):
        """
        Navigate from portal landing page to the Civil, Family & Probate
        Case Records search form.

        Fort Bend has a single 'Fort Bend' location in the dropdown so no
        location selection is needed (default is already correct).
        """
        page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_load_state("networkidle", timeout=15000)

        # Click the Civil records link
        page.get_by_role(
            "link", name="Civil, Family & Probate Case Records"
        ).click()
        page.wait_for_load_state("networkidle", timeout=15000)
        page.wait_for_selector(
            "input[type='submit'][value='Search'], button:has-text('Search')",
            timeout=10000,
        )
        return page

    def search_evictions(self, page, date_from: str, date_to: str):
        """
        Fill Civil records search form for eviction filings in date range.

        Args:
            page: Playwright page already on Civil records search form
            date_from: MM/DD/YYYY (start of date range)
            date_to: MM/DD/YYYY (end of date range)

        Selector strategy: text-based locators with fallbacks for Tyler's
        auto-generated ASP.NET WebForms IDs. May need tuning when first
        exercised against the live portal from Railway.
        """
        # 1. Select "Date Filed" radio button (Search By group)
        try:
            page.get_by_role("radio", name="Date Filed").check()
        except Exception:
            # Fallback: target input[type=radio] containing "Date Filed" value
            page.locator(
                "input[type='radio'][value*='Date Filed' i]"
            ).first.check()

        # Brief wait for conditional date inputs to render
        page.wait_for_timeout(500)

        # 2. Fill date range
        # Tyler legacy typically labels these "Date On or After" /
        # "Date On or Before" or just "On or After" / "On or Before"
        page.get_by_label("On or After", exact=False).first.fill(date_from)
        page.get_by_label("On or Before", exact=False).first.fill(date_to)

        # 3. Select Case Type = Eviction
        # Tyler often uses a multi-select listbox here. select_option
        # works on both single and multi-select.
        try:
            page.get_by_label("Case Type", exact=False).first.select_option(
                label="Eviction"
            )
        except Exception:
            try:
                page.get_by_label("Case Types", exact=False).first.select_option(
                    label="Eviction"
                )
            except Exception as e:
                self.last_error = f"Could not select Eviction case type: {e}"

        # 4. Set Sort By = Case Number (optional - skip if not present)
        try:
            page.get_by_label("Sort By", exact=False).first.select_option(
                label="Case Number"
            )
        except Exception:
            pass

        # 5. Click Search
        page.get_by_role("button", name="Search").click()

        # 6. Wait for results table to render
        page.wait_for_load_state("networkidle", timeout=30000)
        page.wait_for_selector("table", timeout=15000)
        return page

    def session_probe(self, date_from: str = None, date_to: str = None):
        """
        End-to-end navigation probe: launch, navigate to civil search,
        optionally submit a date-range search. Intended for Railway preview
        deploy testing.

        Returns dict with status, url, title, and any error.
        """
        with sync_playwright() as p:
            browser, context, page = self._launch(p)
            try:
                self.navigate_to_civil_search(page)
                result = {
                    "ok": True,
                    "after_navigate_url": page.url,
                    "after_navigate_title": page.title(),
                }
                if date_from and date_to:
                    self.search_evictions(page, date_from, date_to)
                    result["after_search_url"] = page.url
                    result["after_search_title"] = page.title()
                return result
            except Exception as e:
                return {"ok": False, "error": str(e), "url": page.url}
            finally:
                browser.close()


if __name__ == "__main__":
    import sys
    scraper = FortBendTXJPScraper()
    if len(sys.argv) > 1 and sys.argv[1] == "probe":
        # Navigation probe only (no search submit)
        print("Running session probe against tylerpaw.fortbendcountytx.gov...")
        result = scraper.session_probe()
        print(result)
    elif len(sys.argv) > 1 and sys.argv[1] == "search":
        # Full search probe with 7-day window
        from datetime import datetime, timedelta
        end = datetime.now()
        start = end - timedelta(days=7)
        print(f"Running search probe: {start:%m/%d/%Y} -> {end:%m/%d/%Y}")
        result = scraper.session_probe(
            date_from=start.strftime("%m/%d/%Y"),
            date_to=end.strftime("%m/%d/%Y"),
        )
        print(result)
    else:
        print("Smoke test against example.com...")
        result = scraper.smoke_test("https://example.com")
        print(result)