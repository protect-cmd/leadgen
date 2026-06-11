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

import io
import re

import pdfplumber
from playwright.sync_api import sync_playwright

STATE = "TX"
COUNTY = "Fort Bend"
NOTICE_TYPE = "Eviction"
TIMEZONE = "America/Chicago"

PORTAL_BASE = "https://tylerpaw.fortbendcountytx.gov"
PORTAL_URL = f"{PORTAL_BASE}/PublicAccess/default.aspx"

EVICTION_KEYWORDS = ("eviction", "forcible entry")

STREET_SUFFIX_REGEX = (
    r"(?:St|Street|Ave|Avenue|Blvd|Boulevard|"
    r"Dr|Drive|Rd|Road|Ln|Lane|"
    r"Way|Pkwy|Parkway|Pl|Place|"
    r"Ct|Court|Ter|Terrace|Cir|Circle|"
    r"Hwy|Highway|Trl|Trail|"
    r"Loop|Run|Plaza|Sq|Square)"
)

PETITION_ADDRESS_RE = re.compile(
    rf"(\d+\s+[\w\s.,#&\-/]{{1,80}}?\b{STREET_SUFFIX_REGEX}\b\.?"
    rf"(?:\s+(?:#|Apt\.?|Unit|Suite|Ste\.?)\s*[\w\-]+)?)\s*,?\s+"
    rf"([A-Z][a-zA-Z\s.\-]{{1,30}}?),?\s+"
    rf"(TX|Texas)\s+"
    rf"(\d{{5}}(?:-\d{{4}})?)",
    re.IGNORECASE,
)


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
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
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
        Navigate from portal landing page to the Civil records search form.

        Verified DOM (2026-06-09 via user VPN inspection):
        - Civil link: <a class="ssSearchHyperlink"
            href="javascript:LaunchSearch('Search.aspx?ID=400', ...)">
            Civil, Family Case Records</a>
        (NOT "Civil, Family & Probate Case Records" - no Probate)
        - Search button on the form: <input id="SearchSubmit" type="submit">
        """
        try:
            page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception as e:
            self.last_error = f"navigate_to_civil_search: portal load failed: {e}"
            raise

        try:
            page.locator(
                "a.ssSearchHyperlink", has_text="Civil, Family Case Records"
            ).first.click()
        except Exception as e:
            self.last_error = (
                f"navigate_to_civil_search: Civil link click failed - "
                f"expected 'Civil, Family Case Records' class=ssSearchHyperlink: {e}"
            )
            raise

        try:
            page.wait_for_load_state("networkidle", timeout=15000)
            page.wait_for_selector("#SearchSubmit", timeout=10000)
        except Exception as e:
            self.last_error = (
                f"navigate_to_civil_search: search form did not load (#SearchSubmit missing): {e}"
            )
            raise

        return page

    def search_evictions(self, page, date_from: str, date_to: str):
        """
        Fill Civil records search form for eviction filings in date range.

        Verified DOM (2026-06-09):
        - Date Filed radio: id="DateFiled" value="6" with onclick handler
        SwitchCaseSearch(this.value, true) - reveals date inputs on click
        - On or After input: id="DateFiledOnAfter"
        - On or Before input: id="DateFiledOnBefore"
        - Case Type "Evictions" option (PLURAL): value="296"
        - Sort By: id="selectSortBy", option value="casenumber"
        - Search button: id="SearchSubmit" (input type=submit)
        """
        try:
            page.locator("#DateFiled").check()
        except Exception as e:
            self.last_error = (
                f"search_evictions: Date Filed radio (#DateFiled) not clickable: {e}"
            )
            raise

        # SwitchCaseSearch handler renders date inputs conditionally
        page.wait_for_timeout(800)

        try:
            page.locator("#DateFiledOnAfter").fill(date_from)
            page.locator("#DateFiledOnBefore").fill(date_to)
        except Exception as e:
            self.last_error = (
                f"search_evictions: date inputs (#DateFiledOnAfter / #DateFiledOnBefore) "
                f"not fillable: {e}"
            )
            raise

        # Case Type: option text is "Evictions" (PLURAL)
        try:
            case_type_select = page.locator(
                "select:has(option:text-is('Evictions'))"
            ).first
            case_type_select.select_option(label="Evictions")
        except Exception as e:
            self.last_error = (
                f"search_evictions: could not select Evictions case type "
                f"(expected option text 'Evictions' plural): {e}"
            )
            raise

        # Sort By: id=selectSortBy, value=casenumber
        try:
            page.locator("#selectSortBy").select_option(value="casenumber")
        except Exception:
            # Non-critical - default sort (Filed Date) is acceptable
            pass

        try:
            page.locator("#SearchSubmit").click()
        except Exception as e:
            self.last_error = (
                f"search_evictions: Search button (#SearchSubmit) click failed: {e}"
            )
            raise

        try:
            page.wait_for_load_state("networkidle", timeout=30000)
            page.wait_for_selector("table", timeout=15000)
        except Exception as e:
            self.last_error = f"search_evictions: results table did not load: {e}"
            raise

        return page

    def parse_results(self, page) -> list:
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
                row_data["_case_detail_url"] = self._normalize_url(link.get_attribute("href"))
            rows_out.append(row_data)
        return rows_out

    def filter_evictions(self, rows: list) -> list:
        filtered = []
        for row in rows:
            case_type = self._extract_field(row, ("type", "case type", "cause of action"))
            if any(kw in case_type.lower() for kw in EVICTION_KEYWORDS):
                filtered.append(row)
        return filtered

    @staticmethod
    def _extract_field(row: dict, candidate_keys: tuple) -> str:
        for candidate in candidate_keys:
            for k, v in row.items():
                kl = k.lower().strip()
                if candidate in kl:
                    return v
        return ""

    @staticmethod
    def _normalize_url(href: str) -> str:
        if not href:
            return ""
        if href.startswith("http"):
            return href
        if href.startswith("/"):
            return PORTAL_BASE + href
        return f"{PORTAL_BASE}/PublicAccess/" + href

    @staticmethod
    def parse_partial_address(raw: str) -> dict:
        result = {"city": "", "state": "", "zip": ""}
        if not raw:
            return result
        cleaned = raw.strip()
        zip_m = re.search(r"\b(\d{5}(?:-\d{4})?)\s*$", cleaned)
        if not zip_m:
            return result
        result["zip"] = zip_m.group(1)
        rest = cleaned[:zip_m.start()].strip().rstrip(",").strip()
        state_m = re.search(r"\b([A-Z]{2})\s*$", rest)
        if not state_m:
            return result
        result["state"] = state_m.group(1)
        rest = rest[:state_m.start()].strip().rstrip(",").strip()
        result["city"] = rest
        return result

    @staticmethod
    def _grab_after_label(text: str, patterns: list) -> str:
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                return m.group(1).strip()
        return ""

    def parse_case_detail(self, page, case_detail_url: str) -> dict:
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
        detail["case_number"] = self._grab_after_label(
            body_text, [r"Case (?:Number|No\.?|#)\s*:?\s*([^\n]+)"]
        )
        detail["court"] = self._grab_after_label(body_text, [r"\bCourt\s*:?\s*([^\n]+)"])
        detail["filed_date"] = self._grab_after_label(
            body_text, [r"Date Filed\s*:?\s*([^\n]+)", r"File Date\s*:?\s*([^\n]+)"]
        )
        detail["cause_of_action"] = self._grab_after_label(
            body_text, [r"Cause of Action\s*:?\s*([^\n]+)", r"Case Type\s*:?\s*([^\n]+)"]
        )
        plaintiff_match = re.search(
            r"Plaintiff\s*:?\s*([^\n]+(?:\n[^\n]+){0,2})", body_text, re.IGNORECASE
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
                    addr_text = " ".join(lines[1:])
                    parsed = self.parse_partial_address(addr_text)
                    detail["defendant_city"] = parsed["city"]
                    detail["defendant_state"] = parsed["state"]
                    detail["defendant_zip"] = parsed["zip"]
        petition_link = page.query_selector(
            "a:has-text('Original Petition'), a[href*='ViewDocumentFragment']"
        )
        if petition_link:
            href = petition_link.get_attribute("href")
            detail["petition_url"] = self._normalize_url(href)
        return detail

    def fetch_petition_pdf(self, page, petition_url: str) -> bytes:
        if not petition_url:
            return b""
        try:
            response = page.context.request.get(petition_url, timeout=30000)
            if not response.ok:
                self.last_error = f"Petition fetch HTTP {response.status} for {petition_url}"
                return b""
            return response.body()
        except Exception as e:
            self.last_error = f"Petition fetch error: {e}"
            return b""

    @staticmethod
    def extract_petition_text(pdf_bytes: bytes) -> str:
        if not pdf_bytes:
            return ""
        try:
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                parts = [page.extract_text() or "" for page in pdf.pages]
            return "\n".join(parts)
        except Exception:
            return ""

    @staticmethod
    def parse_petition_address(text: str) -> dict:
        result = {"street": "", "city": "", "state": "", "zip": "", "raw": ""}
        if not text:
            return result
        matches = list(PETITION_ADDRESS_RE.finditer(text))
        if not matches:
            return result
        best = matches[0]
        for m in matches:
            preceding = text[max(0, m.start() - 500):m.start()].lower()
            if "defendant" in preceding or "tenant" in preceding:
                best = m
                break
        result["raw"] = best.group(0).strip()
        result["street"] = best.group(1).strip().rstrip(",").strip()
        result["city"] = best.group(2).strip().rstrip(",").strip()
        result["state"] = "TX"
        result["zip"] = best.group(4)
        return result

    def normalize_filing(self, case_detail: dict, petition_address: dict = None) -> dict:
        """
        Convert raw case detail + petition address into the standard Filing schema.

        Fort Bend specifics: judicial_officer / precinct / hearing_date /
        judgment_amount are typically empty (single pooled location, no
        per-judge dropdown, eviction filing doesn't capture hearing or
        disposition data in case detail).

        Street address comes from petition_address (PDF parse) when available;
        falls back to empty street with city/state/zip from case_detail's
        partial-address layer.
        """
        petition_address = petition_address or {}
        street = petition_address.get("street", "")
        city = petition_address.get("city") or case_detail.get("defendant_city", "")
        state = petition_address.get("state") or case_detail.get("defendant_state", "")
        zip_code = petition_address.get("zip") or case_detail.get("defendant_zip", "")
        return {
            "state": STATE,
            "county": COUNTY,
            "notice_type": NOTICE_TYPE,
            "case_number": (case_detail.get("case_number") or "").strip(),
            "court": (case_detail.get("court") or "").strip(),
            "judicial_officer": "",
            "precinct": "",
            "filed_date": (case_detail.get("filed_date") or "").strip(),
            "hearing_date": "",
            "cause_of_action": (case_detail.get("cause_of_action") or "").strip(),
            "plaintiff_name": (case_detail.get("plaintiff_name") or "").strip(),
            "defendant_name": (case_detail.get("defendant_name") or "").strip(),
            "defendant_address_line1": street,
            "defendant_city": city,
            "defendant_state": state,
            "defendant_zip": zip_code,
            "judgment_amount": "",
            "source_url": case_detail.get("source_url", ""),
        }

    @staticmethod
    def dedupe_by_case_number(filings: list) -> list:
        """Remove duplicate filings by case_number, keep first occurrence."""
        seen = set()
        unique = []
        for f in filings:
            cn = (f.get("case_number") or "").strip()
            if not cn or cn in seen:
                continue
            seen.add(cn)
            unique.append(f)
        return unique

    def scrape_all(
        self, date_from: str, date_to: str, fetch_petitions: bool = True
    ) -> dict:
        """
        Full scrape orchestrator for a date range.

        Args:
            date_from, date_to: MM/DD/YYYY (inclusive) date range bounds
            fetch_petitions: if True, fetch Original Petition PDF for each case
                and extract street address. Set False for fast preview (no
                addresses) - useful for selector verification on Railway.

        Returns:
            {
                "ok": bool,
                "filings": [Filing dicts],
                "filings_count": int,
                "address_hit_count": int,
                "address_hit_rate": float (percentage 0-100),
                "raw_row_count": int,
                "eviction_row_count": int,
                "errors": [str],
            }
        """
        filings = []
        errors = []
        raw_count = 0
        eviction_count = 0

        with sync_playwright() as p:
            browser, context, page = self._launch(p)
            try:
                self.navigate_to_civil_search(page)
                self.search_evictions(page, date_from, date_to)

                rows = self.parse_results(page)
                raw_count = len(rows)
                eviction_rows = self.filter_evictions(rows)
                eviction_count = len(eviction_rows)

                for row in eviction_rows:
                    case_url = row.get("_case_detail_url", "")
                    if not case_url:
                        errors.append(
                            f"No case URL for row: {row.get('Case Number', '?')}"
                        )
                        continue

                    try:
                        case_detail = self.parse_case_detail(page, case_url)
                    except Exception as e:
                        errors.append(f"parse_case_detail failed for {case_url}: {e}")
                        continue

                    petition_address = None
                    if fetch_petitions and case_detail.get("petition_url"):
                        try:
                            pdf_bytes = self.fetch_petition_pdf(
                                page, case_detail["petition_url"]
                            )
                            if pdf_bytes:
                                text = self.extract_petition_text(pdf_bytes)
                                if text:
                                    petition_address = self.parse_petition_address(text)
                        except Exception as e:
                            errors.append(
                                "Petition extraction failed for "
                                f"{case_detail.get('case_number', '?')}: {e}"
                            )

                    filing = self.normalize_filing(case_detail, petition_address)
                    filings.append(filing)

            except Exception as e:
                errors.append(f"Scrape session failed: {e}")
                return {
                    "ok": False,
                    "filings": filings,
                    "filings_count": len(filings),
                    "address_hit_count": 0,
                    "address_hit_rate": 0.0,
                    "raw_row_count": raw_count,
                    "eviction_row_count": eviction_count,
                    "errors": errors,
                }
            finally:
                browser.close()

        filings = self.dedupe_by_case_number(filings)
        address_hit = sum(1 for f in filings if f.get("defendant_address_line1"))
        address_hit_rate = (address_hit / len(filings) * 100.0) if filings else 0.0

        return {
            "ok": True,
            "filings": filings,
            "filings_count": len(filings),
            "address_hit_count": address_hit,
            "address_hit_rate": round(address_hit_rate, 2),
            "raw_row_count": raw_count,
            "eviction_row_count": eviction_count,
            "errors": errors,
        }

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
                        first = eviction_rows[0]
                        if first.get("_case_detail_url"):
                            sample = self.parse_case_detail(page, first["_case_detail_url"])
                            result["sample_detail"] = {k: v for k, v in sample.items() if v}
                            if sample.get("petition_url"):
                                pdf_bytes = self.fetch_petition_pdf(page, sample["petition_url"])
                                result["petition_pdf_bytes"] = len(pdf_bytes)
                                if pdf_bytes:
                                    text = self.extract_petition_text(pdf_bytes)
                                    result["petition_text_chars"] = len(text)
                                    if text:
                                        addr = self.parse_petition_address(text)
                                        result["petition_address"] = addr
                return result
            except Exception as e:
                return {"ok": False, "error": str(e), "url": page.url}
            finally:
                browser.close()


if __name__ == "__main__":
    import sys
    import json
    scraper = FortBendTXJPScraper()
    if len(sys.argv) > 1 and sys.argv[1] == "probe":
        print("Running session probe...")
        print(scraper.session_probe())
    elif len(sys.argv) > 1 and sys.argv[1] == "search":
        from datetime import datetime, timedelta
        end = datetime.now()
        start = end - timedelta(days=7)
        print(f"Running search probe: {start:%m/%d/%Y} -> {end:%m/%d/%Y}")
        print(scraper.session_probe(
            date_from=start.strftime("%m/%d/%Y"),
            date_to=end.strftime("%m/%d/%Y"),
        ))
    elif len(sys.argv) > 1 and sys.argv[1] == "full":
        from datetime import datetime, timedelta
        end = datetime.now()
        start = end - timedelta(days=7)
        print(f"Full scrape: {start:%m/%d/%Y} -> {end:%m/%d/%Y}")
        result = scraper.scrape_all(
            date_from=start.strftime("%m/%d/%Y"),
            date_to=end.strftime("%m/%d/%Y"),
        )
        print(json.dumps(result, indent=2, default=str))
    else:
        print("Smoke test against example.com...")
        print(scraper.smoke_test("https://example.com"))