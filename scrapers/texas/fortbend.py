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

import re

from playwright.sync_api import sync_playwright

STATE = "TX"
COUNTY = "Fort Bend"
NOTICE_TYPE = "Eviction"
TIMEZONE = "America/Chicago"

PORTAL_BASE = "https://tylerpaw.fortbendcountytx.gov"
PORTAL_URL = f"{PORTAL_BASE}/PublicAccess/default.aspx"

EVICTION_KEYWORDS = ("eviction", "forcible entry")


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
        """Navigate from portal landing to Civil records search form."""
        page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_load_state("networkidle", timeout=15000)
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
        """Fill Civil records search form for eviction filings in date range."""
        try:
            page.get_by_role("radio", name="Date Filed").check()
        except Exception:
            page.locator(
                "input[type='radio'][value*='Date Filed' i]"
            ).first.check()

        page.wait_for_timeout(500)
        page.get_by_label("On or After", exact=False).first.fill(date_from)
        page.get_by_label("On or Before", exact=False).first.fill(date_to)

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

        try:
            page.get_by_label("Sort By", exact=False).first.select_option(
                label="Case Number"
            )
        except Exception:
            pass

        page.get_by_role("button", name="Search").click()
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
        if not header_cells:
            header_cells = table.query_selector_all("tr:first-child td")
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
            link = row.query_selector(
                "a[href*='CaseDetail'], a[href*='Case.aspx'], a[href*='Case ']"
            )
            if link:
                row_data["_case_detail_url"] = self._normalize_url(
                    link.get_attribute("href")
                )
            rows_out.append(row_data)
        return rows_out

    def filter_evictions(self, rows: list) -> list:
        """Defensive filter - keep only rows whose case-type column contains eviction keyword."""
        filtered = []
        for row in rows:
            case_type = self._extract_field(
                row, ("type", "case type", "cause of action")
            )
            if any(kw in case_type.lower() for kw in EVICTION_KEYWORDS):
                filtered.append(row)
        return filtered

    @staticmethod
    def _extract_field(row: dict, candidate_keys: tuple) -> str:
        """Find first row value whose key contains a candidate (priority order)."""
        for candidate in candidate_keys:
            for k, v in row.items():
                kl = k.lower().strip()
                if candidate in kl:
                    return v
        return ""

    @staticmethod
    def _normalize_url(href: str) -> str:
        """Convert Tyler relative URL to absolute."""
        if not href:
            return ""
        if href.startswith("http"):
            return href
        if href.startswith("/"):
            return PORTAL_BASE + href
        return f"{PORTAL_BASE}/PublicAccess/" + href

    @staticmethod
    def parse_partial_address(raw: str) -> dict:
        """
        Parse a partial defendant address (city + state + zip only).

        Fort Bend case detail pages redact the street address per TX JP
        privacy convention; only city/state/zip appear at this layer. Full
        street address is recovered separately from the Original Petition
        PDF (Step 6).

        Returns dict with city, state, zip keys (empty strings if no match).
        Does NOT raise - missing fields stay empty.
        """
        result = {"city": "", "state": "", "zip": ""}
        if not raw:
            return result

        cleaned = raw.strip()

        # ZIP from end (5 or 5-4)
        zip_m = re.search(r"\b(\d{5}(?:-\d{4})?)\s*$", cleaned)
        if not zip_m:
            return result
        result["zip"] = zip_m.group(1)
        rest = cleaned[:zip_m.start()].strip().rstrip(",").strip()

        # State (2 uppercase) before ZIP
        state_m = re.search(r"\b([A-Z]{2})\s*$", rest)
        if not state_m:
            return result
        result["state"] = state_m.group(1)
        rest = rest[:state_m.start()].strip().rstrip(",").strip()

        # Remaining is city
        result["city"] = rest
        return result

    @staticmethod
    def _grab_after_label(text: str, patterns: list) -> str:
        """Extract first match after any of the label patterns from text."""
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                return m.group(1).strip()
        return ""

    def parse_case_detail(self, page, case_detail_url: str) -> dict:
        """
        Visit Tyler legacy case detail page and extract structured data.

        Returns dict with case_number, court, filed_date, cause_of_action,
        plaintiff_name, defendant_name, defendant_city/state/zip (partial -
        street is hidden here), petition_url (link to Original Petition PDF
        for Step 6 extraction), and source_url.

        Uses label-near-value text extraction. Will need DOM-based
        refinement once Railway preview reveals actual page structure.
        """
        page.goto(case_detail_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_load_state("networkidle", timeout=15000)

        detail = {
            "source_url": page.url,
            "case_number": "",
            "court": "",
            "filed_date": "",
            "cause_of_action": "",
            "plaintiff_name": "",
            "defendant_name": "",
            "defendant_city": "",
            "defendant_state": "",
            "defendant_zip": "",
            "petition_url": "",
        }

        try:
            body_text = page.inner_text("body")
        except Exception as e:
            self.last_error = f"Failed to read body text: {e}"
            return detail

        # Case header
        detail["case_number"] = self._grab_after_label(
            body_text, [r"Case (?:Number|No\.?|#)\s*:?\s*([^\n]+)"]
        )
        detail["court"] = self._grab_after_label(
            body_text, [r"\bCourt\s*:?\s*([^\n]+)"]
        )
        detail["filed_date"] = self._grab_after_label(
            body_text,
            [r"Date Filed\s*:?\s*([^\n]+)", r"File Date\s*:?\s*([^\n]+)"],
        )
        detail["cause_of_action"] = self._grab_after_label(
            body_text,
            [r"Cause of Action\s*:?\s*([^\n]+)", r"Case Type\s*:?\s*([^\n]+)"],
        )

        # Plaintiff
        plaintiff_match = re.search(
            r"Plaintiff\s*:?\s*([^\n]+(?:\n[^\n]+){0,2})",
            body_text,
            re.IGNORECASE,
        )
        if plaintiff_match:
            detail["plaintiff_name"] = (
                plaintiff_match.group(1).strip().split("\n")[0]
            )

        # Defendant + partial address (city/state/zip only)
        defendant_match = re.search(
            r"Defendant\s*:?\s*([^\n]+(?:\n[^\n]+){0,4})",
            body_text,
            re.IGNORECASE,
        )
        if defendant_match:
            block = defendant_match.group(1).strip()
            lines = [l.strip() for l in block.split("\n") if l.strip()]
            if lines:
                detail["defendant_name"] = lines[0]
                if len(lines) > 1:
                    addr_text = " ".join(lines[1:])
                    parsed = self.parse_partial_address(addr_text)
                    detail["defendant_city"] = parsed["city"]
                    detail["defendant_state"] = parsed["state"]
                    detail["defendant_zip"] = parsed["zip"]

        # Find Original Petition link - the critical handoff for Step 6
        petition_link = page.query_selector(
            "a:has-text('Original Petition'), "
            "a[href*='ViewDocumentFragment']"
        )
        if petition_link:
            href = petition_link.get_attribute("href")
            detail["petition_url"] = self._normalize_url(href)

        return detail

    def session_probe(self, date_from: str = None, date_to: str = None):
        """End-to-end navigation probe for Railway preview testing."""
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
                    rows = self.parse_results(page)
                    eviction_rows = self.filter_evictions(rows)
                    result["after_search_url"] = page.url
                    result["row_count"] = len(rows)
                    result["eviction_count"] = len(eviction_rows)
                    if eviction_rows:
                        result["first_row_keys"] = list(eviction_rows[0].keys())
                        # Probe one case detail to verify parser
                        first = eviction_rows[0]
                        if first.get("_case_detail_url"):
                            sample = self.parse_case_detail(
                                page, first["_case_detail_url"]
                            )
                            result["sample_detail_fields"] = {
                                k: v for k, v in sample.items() if v
                            }
                return result
            except Exception as e:
                return {"ok": False, "error": str(e), "url": page.url}
            finally:
                browser.close()


if __name__ == "__main__":
    import sys
    scraper = FortBendTXJPScraper()
    if len(sys.argv) > 1 and sys.argv[1] == "probe":
        print("Running session probe against tylerpaw.fortbendcountytx.gov...")
        result = scraper.session_probe()
        print(result)
    elif len(sys.argv) > 1 and sys.argv[1] == "search":
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