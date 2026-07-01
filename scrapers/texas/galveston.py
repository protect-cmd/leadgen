"""
Galveston County TX JP Courts eviction scraper.

Portal: https://portal.galvestoncountytx.gov/Portal/
Type: Tyler new Odyssey Portal (SPA) - distinct from legacy PublicAccess.
Coverage: JP Pcts 1-4 (Rikard, Apffel, Williams, McCumber).
Geo: Requires US IP (Railway US deployment).
Anti-bot: reCAPTCHA v2 on Search Hearings page.
"""

import re
import time

from playwright.sync_api import sync_playwright
from playwright_stealth import stealth_sync

STATE = "TX"
COUNTY = "Galveston"
NOTICE_TYPE = "Eviction"
TIMEZONE = "America/Chicago"

PORTAL_BASE = "https://portal.galvestoncountytx.gov"
PORTAL_URL = f"{PORTAL_BASE}/Portal/"
SEARCH_HEARINGS_URL = f"{PORTAL_BASE}/Portal/Home/Dashboard/26"

JP_JUDGES = [
    {"name": "Rikard, Gregory L.", "precinct": "JP1"},
    {"name": "Apffel, D. Blake", "precinct": "JP2"},
    {"name": "Williams, Billy A. Jr.", "precinct": "JP3"},
    {"name": "McCumber, Kathleen", "precinct": "JP4"},
]

JP_COURTS = {"JP1", "JP2", "JP3", "JP4"}
EVICTION_KEYWORDS = ("eviction", "forcible entry")

GALVESTON_CITIES = (
    "Clear Lake Shores",
    "Jamaica Beach",
    "Bayou Vista",
    "League City",
    "Tiki Island",
    "Texas City",
    "Friendswood",
    "La Marque",
    "Hitchcock",
    "Dickinson",
    "Galveston",
    "Santa Fe",
    "Bacliff",
    "Kemah",
)

DEFENDANT_BOILERPLATE = (
    "And All Other Occupants",
    "All Other Occupants",
    "and All Other Occupants",
)

MONEY_RE = re.compile(r"\$\s*([\d,]+\.\d{2})")


class GalvestonTXJPScraper:
    """Tyler new Odyssey Portal scraper for Galveston JP1-4 evictions."""

    DEFAULT_USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/127.0.0.0 Safari/537.36"
    )

    def __init__(self):
        self.last_error = None
        self.errors_per_judge = {}

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
                return {"ok": True, "url": page.url, "title": page.title()}
            except Exception as e:
                return {"ok": False, "error": str(e), "url": page.url}
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
        """Attempt reCAPTCHA v2 silent check via stealth. Returns status dict."""
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
                        "note": "Silent check failed; image puzzle present.",
                    }
                time.sleep(0.5)
            return {"status": "error", "error": "timeout waiting for captcha resolution"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def search_judge(self, page, judge_name, date_from, date_to, hearing_type="Civil"):
        """Fill Search Hearings form for one judge and submit."""
        page.get_by_label("Search By").select_option(label="Judicial Officer", timeout=5000)
        page.wait_for_selector(
            "select[name*='judicial' i], select[id*='judicial' i], "
            "label:has-text('Judicial Officer') + * select",
            timeout=5000,
        )
        page.get_by_label("Judicial Officer").select_option(label=judge_name, timeout=5000)
        page.get_by_label("Hearing Type").select_option(label=hearing_type, timeout=5000)
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
        """Parse results table into list of dicts keyed by column header text."""
        rows_out = []
        table = page.query_selector("table")
        if not table:
            return rows_out
        header_cells = table.query_selector_all("thead th")
        if not header_cells:
            header_cells = table.query_selector_all("tr:first-child th")
        headers = [h.inner_text().strip() for h in header_cells]
        body_rows = table.query_selector_all("tbody tr")
        if not body_rows:
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
            link = row.query_selector("a[href*='Case']")
            if link:
                row_data["_case_detail_url"] = self._normalize_case_detail_url(
                    link.get_attribute("href")
                )
            rows_out.append(row_data)
        return rows_out

    def filter_jp_evictions(self, rows: list) -> list:
        """Filter rows to only JP1-4 + eviction cases."""
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
        Find first row value whose key contains a candidate.

        Iterates candidates in priority order (most-specific first), so
        broad fallback keywords like 'date' don't match earlier than
        specific phrases like 'hearing date' or 'file date'. This prevents
        e.g. hearing_date picking up File Date value when File Date column
        appears before Hearing Date in the row.
        """
        for candidate in candidate_keys:
            for k, v in row.items():
                kl = k.lower().strip()
                if candidate in kl:
                    return v
        return ""

    @staticmethod
    def _normalize_case_detail_url(href: str) -> str:
        """Convert relative case-detail URL to absolute."""
        if not href:
            return ""
        if href.startswith("http"):
            return href
        if href.startswith("/"):
            return PORTAL_BASE + href
        return f"{PORTAL_BASE}/Portal/" + href

    @staticmethod
    def parse_address(raw: str) -> dict:
        """Parse a raw address string into street/city/state/zip via Galveston city whitelist."""
        result = {"street": "", "city": "", "state": "", "zip": "", "raw": raw or ""}
        if not raw:
            return result

        cleaned = raw.strip()
        lower = cleaned.lower()
        for boiler in DEFENDANT_BOILERPLATE:
            if lower.startswith(boiler.lower()):
                cleaned = cleaned[len(boiler):].strip()
                break

        zip_m = re.search(r'\b(\d{5}(?:-\d{4})?)\s*$', cleaned)
        if not zip_m:
            return result
        result["zip"] = zip_m.group(1)
        rest = cleaned[:zip_m.start()].strip()

        state_m = re.search(r'\b([A-Z]{2})\s*$', rest)
        if not state_m:
            return result
        result["state"] = state_m.group(1)
        rest = rest[:state_m.start()].strip()

        for city in GALVESTON_CITIES:
            pattern = re.compile(rf'(?:^|\s){re.escape(city)}\s*$', re.IGNORECASE)
            m = pattern.search(rest)
            if m:
                result["city"] = city
                result["street"] = rest[:m.start()].strip().rstrip(',').strip()
                return result

        parts = rest.rsplit(None, 1)
        if len(parts) == 2:
            result["street"] = parts[0].rstrip(',').strip()
            result["city"] = parts[1]
        else:
            result["street"] = rest.rstrip(',').strip()
        return result

    @staticmethod
    def parse_money(raw: str):
        """Parse a dollar amount string to float. Returns None if no match."""
        if not raw:
            return None
        m = MONEY_RE.search(raw)
        if not m:
            return None
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            return None

    def parse_case_detail(self, page, case_detail_url: str) -> dict:
        """Visit case detail page and extract structured data."""
        page.goto(case_detail_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_load_state("networkidle", timeout=15000)

        detail = {
            "source_url": page.url,
            "case_number": "",
            "court": "",
            "judicial_officer": "",
            "cause_of_action": "",
            "plaintiff_name": "",
            "defendant_name": "",
            "defendant_address_raw": "",
            "defendant_street": "",
            "defendant_city": "",
            "defendant_state": "",
            "defendant_zip": "",
            "judgment_amount": None,
        }

        try:
            body_text = page.inner_text("body")
        except Exception as e:
            self.last_error = f"Failed to read body text: {e}"
            return detail

        def _grab_after_label(label_patterns, text):
            for pat in label_patterns:
                m = re.search(pat, text, re.IGNORECASE)
                if m:
                    return m.group(1).strip()
            return ""

        detail["case_number"] = _grab_after_label(
            [r"Case (?:Number|No\.?)\s*:?\s*([^\n]+)"], body_text
        )
        detail["court"] = _grab_after_label(
            [r"\bCourt\s*:?\s*(JP\d|District|County[^\n]+)"], body_text
        )
        detail["judicial_officer"] = _grab_after_label(
            [r"Judicial Officer\s*:?\s*([^\n]+)"], body_text
        )
        detail["cause_of_action"] = _grab_after_label(
            [r"Cause of Action\s*:?\s*([^\n]+)"], body_text
        )
        detail["judgment_amount"] = self.parse_money(
            _grab_after_label([r"Judgment Amount\s*:?\s*([^\n]+)"], body_text)
        )

        plaintiff_match = re.search(
            r"Plaintiff\s*:?\s*([^\n]+(?:\n[^\n]+)?)", body_text, re.IGNORECASE
        )
        if plaintiff_match:
            detail["plaintiff_name"] = plaintiff_match.group(1).strip().split("\n")[0]

        defendant_match = re.search(
            r"Defendant\s*:?\s*([^\n]+(?:\n[^\n]+){0,4})", body_text, re.IGNORECASE
        )
        if defendant_match:
            block = defendant_match.group(1).strip()
            lines = [l.strip() for l in block.split("\n") if l.strip()]
            if lines:
                detail["defendant_name"] = lines[0]
                if len(lines) > 1:
                    address_lines = " ".join(lines[1:])
                    detail["defendant_address_raw"] = address_lines
                    parsed = self.parse_address(address_lines)
                    detail["defendant_street"] = parsed["street"]
                    detail["defendant_city"] = parsed["city"]
                    detail["defendant_state"] = parsed["state"]
                    detail["defendant_zip"] = parsed["zip"]
        return detail

    def _normalize_filing(self, row: dict, detail: dict, judge: dict) -> dict:
        """
        Normalize scraped row + detail into standard Filing schema dict.

        Combines data from the results-table row (basic info) with the
        case-detail page (defendant address, judgment amount, party info).
        Detail values take precedence over row values when both exist.
        """
        return {
            "state": STATE,
            "county": COUNTY,
            "notice_type": NOTICE_TYPE,
            "case_number": (
                detail.get("case_number")
                or self._extract_field(row, ("case", "case number", "case #"))
            ),
            "court": (
                detail.get("court")
                or self._extract_field(row, ("court", "location"))
            ),
            "judicial_officer": detail.get("judicial_officer") or judge["name"],
            "precinct": judge["precinct"],
            "filed_date": self._extract_field(
                row, ("file date", "filed", "date filed")
            ),
            "hearing_date": self._extract_field(
                row, ("hearing date", "hearing", "setting date", "date")
            ),
            "cause_of_action": (
                detail.get("cause_of_action")
                or self._extract_field(row, ("type", "case type", "cause of action"))
            ),
            "style": self._extract_field(row, ("style",)),
            "plaintiff_name": detail.get("plaintiff_name", ""),
            "defendant_name": detail.get("defendant_name", ""),
            "defendant_address_line1": detail.get("defendant_street", ""),
            "defendant_address_city": detail.get("defendant_city", ""),
            "defendant_address_state": detail.get("defendant_state", ""),
            "defendant_address_zip": detail.get("defendant_zip", ""),
            "defendant_address_raw": detail.get("defendant_address_raw", ""),
            "judgment_amount": detail.get("judgment_amount"),
            "source_url": detail.get("source_url", ""),
        }

    def scrape_all_judges(
        self,
        date_from: str,
        date_to: str,
        fetch_details: bool = True,
    ) -> list:
        """
        Top-level orchestrator: loop all 4 JP judges, scrape evictions for
        the given date range, optionally fetch case detail, normalize to
        standard Filing schema, deduplicate by case_number.

        Args:
            date_from: MM/DD/YYYY (start of date range)
            date_to: MM/DD/YYYY (end of date range)
            fetch_details: If True (default), visit each case detail page
                to extract defendant address + judgment amount. Set False
                for a faster preview/smoke run.

        Returns:
            List of normalized filing dicts, deduplicated by case_number.

        Side effects:
            self.errors_per_judge populated with judge_name -> error_msg
            for any judges that failed mid-scrape.
        """
        all_filings = []
        seen_case_numbers = set()
        self.errors_per_judge = {}

        with sync_playwright() as p:
            browser, context, page = self._launch(p)
            try:
                for judge in JP_JUDGES:
                    try:
                        self.navigate_to_search_hearings(page)

                        if self.check_captcha_present(page):
                            captcha_result = self.attempt_captcha(page)
                            if captcha_result["status"] != "passed":
                                self.errors_per_judge[judge["name"]] = (
                                    f"Captcha failed: {captcha_result}"
                                )
                                continue

                        self.search_judge(
                            page, judge["name"], date_from, date_to, "Civil"
                        )
                        rows = self.parse_results(page)
                        eviction_rows = self.filter_jp_evictions(rows)

                        for row in eviction_rows:
                            case_num = self._extract_field(
                                row, ("case", "case number", "case #")
                            )
                            if not case_num or case_num in seen_case_numbers:
                                continue
                            seen_case_numbers.add(case_num)

                            detail = {}
                            if fetch_details and row.get("_case_detail_url"):
                                try:
                                    detail = self.parse_case_detail(
                                        page, row["_case_detail_url"]
                                    )
                                except Exception as e:
                                    detail = {}
                                    self.errors_per_judge[
                                        f"{judge['name']}/{case_num}"
                                    ] = f"Detail fetch failed: {e}"

                            filing = self._normalize_filing(row, detail, judge)
                            all_filings.append(filing)

                    except Exception as e:
                        self.errors_per_judge[judge["name"]] = str(e)
                        continue

                return all_filings
            finally:
                browser.close()

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
                return {"ok": False, "error": str(e), "url": page.url}
            finally:
                browser.close()


if __name__ == "__main__":
    import sys
    scraper = GalvestonTXJPScraper()
    if len(sys.argv) > 1 and sys.argv[1] == "probe":
        print("Running session probe against portal.galvestoncountytx.gov...")
        result = scraper.session_probe()
        print(result)
    elif len(sys.argv) > 1 and sys.argv[1] == "scrape":
        # Real scrape with default 7-day window
        from datetime import datetime, timedelta
        end = datetime.now()
        start = end - timedelta(days=7)
        result = scraper.scrape_all_judges(
            date_from=start.strftime("%m/%d/%Y"),
            date_to=end.strftime("%m/%d/%Y"),
        )
        print(f"Scraped {len(result)} filings")
        if scraper.errors_per_judge:
            print(f"Errors: {scraper.errors_per_judge}")
        for f in result[:3]:
            print(f)
    else:
        print("Smoke test against example.com...")
        result = scraper.smoke_test("https://example.com")
        print(result)