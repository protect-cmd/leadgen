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

JP_COURTS = {"JP1", "JP2", "JP3", "JP4"}
EVICTION_KEYWORDS = ("eviction", "forcible entry")


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

    def search_judge(
        self,
        page,
        judge_name: str,
        date_from: str,
        date_to: str,
        hearing_type: str = "Civil",
    ):
        """Fill Search Hearings form for one judge and submit."""
        page.get_by_label("Search By").select_option(
            label="Judicial Officer", timeout=5000
        )
        page.wait_for_selector(
            "select[name*='judicial' i], select[id*='judicial' i], "
            "label:has-text('Judicial Officer') + * select",
            timeout=5000,
        )
        page.get_by_label("Judicial Officer").select_option(
            label=judge_name, timeout=5000
        )
        page.get_by_label("Hearing Type").select_option(
            label=hearing_type, timeout=5000
        )
        page.get_by_label("Date From").fill(date_from)
        page.get_by_label("Date To").fill(date_to)
        page.get_by_role("button", name="Search").click()

        try:
            page.wait_for_timeout(2000)
            if self.check_captcha_present(page):
                captcha_result = self.attempt_captcha(page)
                if captcha_result["status"] != "passed":
                    self.last_error = f"Captcha not passed: {captcha_result}"
                    return page
        except Exception as e:
            self.last_error = f"Captcha check error: {e}"

        page.wait_for_load_state("networkidle", timeout=30000)
        page.wait_for_selector("table", timeout=15000)
        return page

    def parse_results(self, page) -> list:
        """
        Parse the results table on the Search Hearings results page.

        Returns list of dicts, one per row. Keys are column header text;
        values are cell text. Also includes '_case_detail_url' if a
        case-detail link is found in the row.

        Resilient to column ordering changes - relies on header text not
        column position. Tolerates missing tbody.
        """
        rows_out = []
        table = page.query_selector("table")
        if not table:
            return rows_out

        # Extract column headers (try thead first, fallback to first tr)
        header_cells = table.query_selector_all("thead th")
        if not header_cells:
            header_cells = table.query_selector_all("tr:first-child th")
        headers = [h.inner_text().strip() for h in header_cells]

        # Parse body rows
        body_rows = table.query_selector_all("tbody tr")
        if not body_rows:
            # Tables without explicit tbody - skip header row
            all_rows = table.query_selector_all("tr")
            body_rows = all_rows[1:] if len(all_rows) > 1 else []

        for row in body_rows:
            cells = row.query_selector_all("td")
            if not cells:
                continue
            row_data = {}
            for i, cell in enumerate(cells):
                key = headers[i] if i < len(headers) else f"col_{i}"
                row_data[key] = cell.inner_text().strip()

            # Try to extract case detail link
            link = row.query_selector("a[href*='Case']")
            if link:
                row_data["_case_detail_url"] = link.get_attribute("href")

            rows_out.append(row_data)

        return rows_out

    def filter_jp_evictions(self, rows: list) -> list:
        """
        Filter scraped result rows to only JP eviction cases.

        Keeps rows matching ALL of:
          - Court column contains JP1/JP2/JP3/JP4
          - Case type contains 'eviction' or 'forcible entry'
            OR case number matches the 26-EV0N-XXXX pattern (EV0 substring)

        Header keys are matched case-insensitively and flexibly to handle
        variation in how Tyler labels columns (Court vs Location, Type vs
        Case Type, etc.).
        """
        filtered = []
        for row in rows:
            court = self._extract_field(row, ("court", "location"))
            case_type = self._extract_field(row, ("type", "case type", "cause of action"))
            case_num = self._extract_field(row, ("case", "case number", "case #"))

            is_jp = any(jp in court for jp in JP_COURTS)
            is_eviction = (
                any(kw in case_type.lower() for kw in EVICTION_KEYWORDS)
                or "EV0" in case_num
            )

            if is_jp and is_eviction:
                filtered.append(row)
        return filtered

    @staticmethod
    def _extract_field(row: dict, candidate_keys: tuple) -> str:
        """
        Find the first row value whose key (case-insensitive) matches any
        of the candidate keys. Returns empty string if none match.
        """
        for k, v in row.items():
            kl = k.lower().strip()
            for candidate in candidate_keys:
                if candidate in kl:
                    return v
        return ""

    def session_probe(self):
        """End-to-end session probe for Railway preview testing."""
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
        print("Running session probe against portal.galvestoncountytx.gov...")
        result = scraper.session_probe()
    else:
        print("Smoke test against example.com...")
        result = scraper.smoke_test("https://example.com")
    print(result)