"""
Fort Bend County TX JP Courts eviction scraper.

Portal: https://tylerpaw.fortbendcountytx.gov/PublicAccess/default.aspx
Type: Tyler legacy PublicAccess (same template as Denton/Montgomery).
Coverage: All JP precincts pooled under single 'Fort Bend' location entry.
Geo: Requires US IP (Railway US deployment) - .gov subdomain.
Anti-bot: Cloudflare on main county site; tylerpaw subdomain accessible.

Address recovery (two-stage):
  Case detail page hides street; clicking 'Original Petition' navigates
  to CPR.aspx event page which lists 1+ filed documents (petition,
  lease, exhibits). Each document opens via ViewDocumentFragment.aspx
  as PDF. We fetch ALL documents for the event and parse address from
  combined text (petition AND lease both contain defendant address per
  TX Justice Court Rule 510.3, giving us redundancy).

Case number parsing: '26-JEV11-02324' format encodes year, JP court,
case type (EV=eviction), precinct (1), court_num (1), sequence.
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

# Fort Bend JP case number format: YY-J<CASE_TYPE><PRECINCT><COURT_NUM>-NNNNN
# Example: '26-JEV11-02324' => year=26, type=EV, precinct=1, court=1, seq=02324
CASE_NUMBER_RE = re.compile(r"^(\d{2})-J([A-Z]+)(\d)(\d)-(\d+)$")


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
                f"navigate_to_civil_search: Civil link click failed: {e}"
            )
            raise
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
            page.wait_for_selector("#SearchSubmit", timeout=10000)
        except Exception as e:
            self.last_error = (
                f"navigate_to_civil_search: search form did not load: {e}"
            )
            raise
        return page

    def search_evictions(self, page, date_from: str, date_to: str):
        try:
            page.locator("#DateFiled").check()
        except Exception as e:
            self.last_error = f"search_evictions: Date Filed radio not clickable: {e}"
            raise
        page.wait_for_timeout(800)
        try:
            page.locator("#DateFiledOnAfter").fill(date_from)
            page.locator("#DateFiledOnBefore").fill(date_to)
        except Exception as e:
            self.last_error = f"search_evictions: date inputs not fillable: {e}"
            raise
        try:
            case_type_select = page.locator(
                "select:has(option:text-is('Evictions'))"
            ).first
            case_type_select.select_option(label="Evictions")
        except Exception as e:
            self.last_error = (
                f"search_evictions: could not select Evictions case type: {e}"
            )
            raise
        try:
            page.locator("#selectSortBy").select_option(value="casenumber")
        except Exception:
            pass
        try:
            page.locator("#SearchSubmit").click()
        except Exception as e:
            self.last_error = f"search_evictions: Search button click failed: {e}"
            raise
        try:
            page.wait_for_load_state("networkidle", timeout=30000)
            page.wait_for_selector("table", timeout=15000)
        except Exception as e:
            self.last_error = f"search_evictions: results table did not load: {e}"
            raise
        return page

    def parse_results(self, page) -> list:
        """
        Parse Tyler legacy PublicAccess results grid into list of dicts.

        Tyler results pages contain MULTIPLE layout tables with no
        class/id to distinguish them. Document-order selection ('table')
        grabs the first nav/header layout table - empty data, looks
        like 0 results. Boss live-tested 2026-06-09: 7 tables on the
        results page, real data grid is at index 5 with 63 rows / 60
        case-detail links.

        Strategy: select the table containing the MOST case-detail
        links (CaseID or CaseDetail in href). This signal is portable
        across Tyler installs since case-detail link patterns are
        universal, and is robust to table reorderings or chrome
        additions.
        """
        rows_out = []

        tables = page.query_selector_all("table")
        if not tables:
            return rows_out

        # Find the table with the most case-detail links - that's the
        # data grid. Layout tables have 0; data grid has dozens.
        table = max(
            tables,
            key=lambda t: len(
                t.query_selector_all("a[href*='CaseID'], a[href*='CaseDetail']")
            ),
            default=None,
        )
        if not table:
            return rows_out

        # If even the best table has 0 case-detail links, results
        # are genuinely empty - bail rather than parse layout chrome
        link_count = len(
            table.query_selector_all("a[href*='CaseID'], a[href*='CaseDetail']")
        )
        if link_count == 0:
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
                "a[href*='CaseDetail'], a[href*='CaseID'], a[href*='Case.aspx']"
            )
            if link:
                row_data["_case_detail_url"] = self._normalize_url(
                    link.get_attribute("href")
                )
            rows_out.append(row_data)
        return rows_out

    def filter_evictions(self, rows: list) -> list:
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
    def parse_case_number(case_number: str) -> dict:
        """
        Parse Fort Bend JP case number to extract metadata.

        Format: YY-J<CASE_TYPE_CODE><PRECINCT><COURT_NUM>-NNNNN
        Example: '26-JEV11-02324'
          - year: '26'
          - case_type_code: 'EV' (Eviction)
          - precinct: '1'
          - court_num: '1'
          - sequence: '02324'

        Returns dict with year/case_type_code/precinct/court_num/sequence
        keys (empty strings if pattern doesn't match).
        """
        result = {
            "year": "",
            "case_type_code": "",
            "precinct": "",
            "court_num": "",
            "sequence": "",
        }
        if not case_number:
            return result
        m = CASE_NUMBER_RE.match(case_number.strip())
        if not m:
            return result
        result["year"] = m.group(1)
        result["case_type_code"] = m.group(2)
        result["precinct"] = m.group(3)
        result["court_num"] = m.group(4)
        result["sequence"] = m.group(5)
        return result

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
        """
        Visit case detail (CaseDetail.aspx) page, extract structured data
        plus the URL of the Original Petition EVENT page (CPR.aspx).

        IMPORTANT: petition_event_url is the CPR.aspx page, NOT the PDF
        itself. The PDF(s) live one level deeper at ViewDocumentFragment.aspx
        and must be discovered via fetch_event_document_urls().
        """
        try:
            page.goto(case_detail_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception as e:
            self.last_error = f"parse_case_detail: case page load failed: {e}"
            return {"source_url": case_detail_url, "petition_event_url": ""}

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
            "petition_event_url": "",
        }

        try:
            body_text = page.inner_text("body")
        except Exception as e:
            self.last_error = f"parse_case_detail: body text read failed: {e}"
            return detail

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

        plaintiff_match = re.search(
            r"Plaintiff\s*:?\s*([^\n]+(?:\n[^\n]+){0,2})",
            body_text,
            re.IGNORECASE,
        )
        if plaintiff_match:
            detail["plaintiff_name"] = (
                plaintiff_match.group(1).strip().split("\n")[0]
            )

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

        # Original Petition link points to CPR.aspx event page (NOT a PDF)
        # Example: <a href="CPR.aspx?CaseID=2699909&EventID=36268804&CaseCategoryKeys=CV">
        petition_link = page.query_selector(
            "a[href*='CPR.aspx']:has-text('Original Petition'), "
            "a:has-text('Original Petition')"
        )
        if petition_link:
            detail["petition_event_url"] = self._normalize_url(
                petition_link.get_attribute("href")
            )

        return detail

    def fetch_event_document_urls(self, page, event_url: str) -> list:
        """
        Visit CPR.aspx event page, return list of absolute
        ViewDocumentFragment.aspx URLs (one per attached document).

        Fort Bend Original Petition events typically include multiple
        attached documents (petition, lease, exhibits). All contain useful
        data; we fetch every one and extract text from the combined
        contents.

        Returns empty list if event_url is missing or no documents found.
        """
        if not event_url:
            return []
        try:
            page.goto(event_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception as e:
            self.last_error = f"fetch_event_document_urls: event page load failed: {e}"
            return []
        doc_links = page.query_selector_all("a[href*='ViewDocumentFragment.aspx']")
        urls = []
        for link in doc_links:
            href = link.get_attribute("href")
            if href:
                urls.append(self._normalize_url(href))
        return urls

    def fetch_petition_pdf(self, page, document_url: str) -> bytes:
        """
        Fetch a single document PDF via shared Playwright session cookies.

        SecurityToken in the URL is likely cookie-bound so we use
        page.context.request which carries the session cookies, not a
        cold httpx.get().
        """
        if not document_url:
            return b""
        try:
            response = page.context.request.get(document_url, timeout=30000)
            if not response.ok:
                self.last_error = (
                    f"fetch_petition_pdf: HTTP {response.status} for {document_url}"
                )
                return b""
            return response.body()
        except Exception as e:
            self.last_error = f"fetch_petition_pdf: error: {e}"
            return b""

    @staticmethod
    def extract_petition_text(pdf_bytes: bytes) -> str:
        if not pdf_bytes:
            return ""
        try:
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                parts = [pg.extract_text() or "" for pg in pdf.pages]
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

    def extract_address_from_event(self, page, event_url: str) -> dict:
        """
        Two-stage extraction: visit CPR.aspx event page, list all
        documents (petition + lease + any exhibits), fetch each as PDF,
        extract text from all of them combined, then parse defendant
        address from combined text.

        Combined-text approach intentionally redundant: both petition
        and lease per TX Rule 510.3 contain defendant's address, so if
        one document fails to extract (scanned image, broken PDF), the
        other usually still works.
        """
        doc_urls = self.fetch_event_document_urls(page, event_url)
        if not doc_urls:
            return {"street": "", "city": "", "state": "", "zip": "", "raw": ""}
        combined_text_parts = []
        for url in doc_urls:
            pdf_bytes = self.fetch_petition_pdf(page, url)
            if pdf_bytes:
                text = self.extract_petition_text(pdf_bytes)
                if text:
                    combined_text_parts.append(text)
        if not combined_text_parts:
            return {"street": "", "city": "", "state": "", "zip": "", "raw": ""}
        combined = "\n".join(combined_text_parts)
        return self.parse_petition_address(combined)

    def normalize_filing(self, case_detail: dict, petition_address: dict = None) -> dict:
        petition_address = petition_address or {}
        street = petition_address.get("street", "")
        city = petition_address.get("city") or case_detail.get("defendant_city", "")
        state = petition_address.get("state") or case_detail.get("defendant_state", "")
        zip_code = petition_address.get("zip") or case_detail.get("defendant_zip", "")

        case_number = (case_detail.get("case_number") or "").strip()
        case_meta = self.parse_case_number(case_number)

        return {
            "state": STATE,
            "county": COUNTY,
            "notice_type": NOTICE_TYPE,
            "case_number": case_number,
            "court": (case_detail.get("court") or "").strip(),
            "judicial_officer": "",
            "precinct": case_meta.get("precinct", ""),
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
                    if fetch_petitions and case_detail.get("petition_event_url"):
                        try:
                            petition_address = self.extract_address_from_event(
                                page, case_detail["petition_event_url"]
                            )
                        except Exception as e:
                            errors.append(
                                "extract_address_from_event failed for "
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
                            sample = self.parse_case_detail(
                                page, first["_case_detail_url"]
                            )
                            result["sample_detail"] = {
                                k: v for k, v in sample.items() if v
                            }
                            if sample.get("petition_event_url"):
                                addr = self.extract_address_from_event(
                                    page, sample["petition_event_url"]
                                )
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
        print("Running session probe against tylerpaw.fortbendcountytx.gov...")
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