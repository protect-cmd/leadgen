from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, timedelta
from typing import Optional

import fitz
import pytesseract
from PIL import Image
from playwright.async_api import async_playwright

from models.filing import Filing
from scrapers.base_scraper import BaseScraper
from scrapers.dates import court_today
from services.name_utils import clean_tenant_name

log = logging.getLogger(__name__)

# Sarasota County Clerk — ClerkNet 3.0 (custom ASP.NET portal).
# No bot protection; local Chromium works fine (Bright Data blocks .gov domains).
#
# Portal flow:
#   1. AnonLanding.aspx — click "Agree" to establish anonymous session.
#   2. Search.aspx — Telerik RadComboBox for Court Type ("Civil") + Case Type
#      ("Evictions"); RadDatePicker for date range.  Results in a RadGrid with
#      input.rgPageNext pagination (remove onclick="return false;" before clicking).
#   3. Per-case: search by case number → click result → CaseInfo.aspx loads.
#      (direct.aspx?caseid= GUIDs are session-scoped; goto() on them → Error.aspx.)
#   4. Docket row matching "COMPLAINT FOR EVICTION" → ViewPDF.aspx?id=GUID download.
#   5. OCR page 1 → address extracted by one of three regex patterns (see _parse_address).
#
# Telerik quirks (July 2026):
#   - Court Type: open arrow → force-click li (triggers AutoPostBack AJAX);
#     must also patch CourtType.ClientState.value="1" before submitting.
#   - Case Type: lazy-loaded; open arrow first, wait 2s, then findItemByText().select();
#     must patch CaseType.ClientState.checkedIndices=[4] (Evictions is index 4).
#   - Date pickers: fill "#rdStart_dateInput" + Tab (not rdpDateFrom).
#
# Volume: ~5 residential evictions per day. Each case downloads a ~7 MB PDF + OCR.

PORTAL_URL     = "https://secure.sarasotaclerk.com"
LANDING_URL    = f"{PORTAL_URL}/AnonLanding.aspx"
SEARCH_URL     = f"{PORTAL_URL}/Search.aspx"
SOURCE_URL     = SEARCH_URL

STATE          = "FL"
COUNTY         = "Sarasota"
COURT_TIMEZONE = "America/New_York"
NOTICE_TYPE    = "Residential Eviction"

COURT_TYPE_LABEL = "Civil"
CASE_TYPE_LABEL  = "Evictions"

# Delay between per-case fetches (seconds); each fetch downloads a ~7 MB PDF.
_REQUEST_DELAY = float(os.getenv("SARASOTA_REQUEST_DELAY", "2.0"))

# Telerik control IDs confirmed from live DOM inspection (July 2026).
# Court Type dropdown: open arrow → force-click .rcbItem li (AutoPostBack AJAX fires).
# Case Type dropdown: lazy-loaded — open arrow first, wait 2s, then findItemByText().select().
# Date pickers use rdStart / rdEnd (not rdpDateFrom / rdpDateTo).
_COURT_TYPE_CTRL   = "ctl00_cphBody_rcbCourtType"
_CASE_TYPE_CTRL    = "ctl00_cphBody_rcbCaseType"
_COURT_TYPE_ARROW  = "#ctl00_cphBody_rcbCourtType_Arrow"
_CASE_TYPE_ARROW   = "#ctl00_cphBody_rcbCaseType_Arrow"
_DATE_FROM_ID      = "ctl00_cphBody_rdStart_dateInput"
_DATE_TO_ID        = "ctl00_cphBody_rdEnd_dateInput"
_SEARCH_BTN        = "#ctl00_cphBody_bSearch_input"

# Tesseract path — Windows default install location.
_TESSERACT_CMD = os.getenv(
    "TESSERACT_CMD", r"C:\Program Files\Tesseract-OCR\tesseract.exe"
)

# Multiple address patterns cover Sarasota complaint form variants (three observed):
#
# 1. Attorney long-form (EXECUTIVE HOUSE style):
#    "Plaintiff owns real property in this county described as:\n  <STREET>\n  <CITY, FL ZIP>"
#
# 2. Florida Bar pro-se form:
#    "Plaintiff owns the following described real property in said county:\n  [<STREET CITY FL ZIP>]"
#
# 3. Attorney tenant-eviction form (SARATOGA style):
#    "possession of the residential property described as follows:\n  <STREET CITY, FL ZIP>"
#    or "the property described as <STREET>, <CITY>, FL <ZIP>"
_ADDR_RE_ATTORNEY = re.compile(
    r"owns real property in this county described as[:\s]+\n\s*(.+?)\n\s*(.+?(?:Florida|FL)\s+\d{5})",
    re.IGNORECASE,
)
_ADDR_RE_PROSE = re.compile(
    r"owns the following described real property[^\n]*\n\s*[\[\(]?\s*(.+?(?:FL|Florida)\s+\d{5})\s*[\]\)]?",
    re.IGNORECASE,
)
_ADDR_RE_DESCRIBED = re.compile(
    r"(?:property\s+described[\s\n]+as(?:\s+follows)?[:\s]*[\n\r\s]*)(.+?(?:FL|Florida)\s+\d{5})",
    re.IGNORECASE,
)
# Multi-line variant: property name/apt on line(s) before the street+FL zip.
# Handles: "described as:\n<PROPERTY NAME>\n<STREET>\n<CITY, FL ZIP>"
_ADDR_RE_DESCRIBED_ML = re.compile(
    r"described\s+as[:\s]*\n(?:[^\n]*\n){0,2}(\d[^\n]+)\n([^\n]+(?:FL|Florida)\s+\d{5}[^\n]*)",
    re.IGNORECASE,
)
# Checkbox form: "PLAINTIFF VS. DEFENDANT\n<P NAME ADDR> | <D NAME ADDR>"
# Defendant's address (= rental property) is the last FL-zip segment after the pipe.
_ADDR_RE_HEADER = re.compile(
    r"PLAINTIFF\s+VS\.\s+DEFENDANT[^\n]*\n([^\n]+FL\s+\d{5}[^\n]*)",
    re.IGNORECASE,
)
# Pro-se Florida court form — two sub-variants:
# A) Street on line above "Address" label:
#    "<STREET>\nAddress[:]?\n<CITY, ZIP>\nCity, State, Zip Code"
# B) Address inline after colon:
#    "Address: <STREET>\n<CITY, ZIP>\nCity, State, Zip Code"
# OCR often misreads "FL" as "Ft", "Fu", "Fi" — use zip as anchor.
_ADDR_RE_LABEL = re.compile(
    r"(\d[^\n]+)\s*\nAddress:?[^\n]*\n+([^\n]+\b\d{5}[^\n]*)\s*\nCity,?\s*State",
    re.IGNORECASE,
)
_ADDR_RE_LABEL_INLINE = re.compile(
    r"Address:\s*([^\n]+\d[^\n]*)\n+([^\n]+\b\d{5}[^\n]*)\s*\nCity,?\s*State",
    re.IGNORECASE,
)


def _normalize_city_zip(raw: str) -> str:
    """Normalize OCR city/state/zip: fix common FL misreads, collapse whitespace."""
    s = re.sub(r"\s+", " ", raw).strip().rstrip(".,")
    # Fix OCR misreads of "FL": Ft, Fu, Fi, F|, Fl → FL
    s = re.sub(r"\bF[tTuUiI|l]\.?\b", "FL", s)
    s = re.sub(r"\bFlorida\b", "FL", s, flags=re.IGNORECASE)
    # Ensure space between FL and zip if missing: "FL.34232" → "FL 34232"
    s = re.sub(r"\bFL\.?\s*(\d{5})", r"FL \1", s)
    return s


def _post_normalize(addr: str) -> str:
    """Final cleanup on any extracted address: fix FL. / FL spacing issues."""
    addr = re.sub(r"\bFL\.?\s*(\d{5})", r"FL \1", addr)
    addr = re.sub(r"\s+", " ", addr).strip()
    return addr


class SarasotaScraper(BaseScraper):
    """
    Scrapes Sarasota County ClerkNet 3.0 for residential eviction filings (VDG).

    Per-case flow: open CaseInfo.aspx → download Complaint for Eviction PDF from
    docket → OCR page 1 → extract property address from paragraph 2.
    Requires Tesseract OCR installed at TESSERACT_CMD (default Windows path).
    """

    def __init__(self, lookback_days: int = 2, headless: bool = True):
        super().__init__(headless=headless)
        self.lookback_days = lookback_days
        self.last_error: Optional[str] = None
        pytesseract.pytesseract.tesseract_cmd = _TESSERACT_CMD

    async def scrape(self) -> list[Filing]:
        self.last_error = None
        today = court_today(COURT_TIMEZONE)
        start = today - timedelta(days=self.lookback_days)
        filings: list[Filing] = []

        page = await self._launch_browser()
        try:
            log.info("Sarasota FL: establishing anonymous session")
            await page.goto(LANDING_URL, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(1_500)
            await page.click("#cphBody_bAgree", timeout=10_000)
            await page.wait_for_timeout(1_500)

            log.info("Sarasota FL: searching eviction filings")
            filings = await self._search(page, start, today)

        except Exception as e:
            self.last_error = str(e)
            log.error("Sarasota FL: scrape failed: %s", e, exc_info=True)
        finally:
            await self._close_browser()

        if not filings:
            if not self.last_error:
                self.last_error = "zero evictions returned; possible block or empty window"
            log.warning("Sarasota FL: %s", self.last_error)
            return []

        unique = {f.case_number: f for f in filings}
        result = list(unique.values())
        log.info("Sarasota FL: %d filings found", len(result))
        return result

    # ------------------------------------------------------------------ #
    #  Browser launch                                                      #
    # ------------------------------------------------------------------ #

    async def _launch_browser(self):
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await self._browser.new_context(
            accept_downloads=True,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        page = await context.new_page()
        await page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        return page

    # ------------------------------------------------------------------ #
    #  Search                                                              #
    # ------------------------------------------------------------------ #

    async def _search(self, page, start: date, today: date) -> list[Filing]:
        await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(1_500)

        # Court Type: open dropdown arrow → force-click "Civil" li.
        # force=True bypasses Playwright's visibility check; the click fires the
        # full Telerik + AutoPostBack chain that populates Case Type options.
        await page.click(_COURT_TYPE_ARROW)
        await page.wait_for_timeout(600)
        await page.click(
            f"#ctl00_cphBody_rcbCourtType_DropDown li.rcbItem:has-text('{COURT_TYPE_LABEL}')",
            force=True,
            timeout=5_000,
        )
        await page.wait_for_timeout(3_000)

        # Case Type: open dropdown (triggers lazy AJAX item load), then select
        # "Evictions" via Telerik findItemByText — works once items are loaded.
        await page.click(_CASE_TYPE_ARROW)
        await page.wait_for_timeout(2_000)
        found = await page.evaluate(f"""() => {{
            const cb = $find('{_CASE_TYPE_CTRL}');
            const item = cb.findItemByText('{CASE_TYPE_LABEL}');
            if (!item) return false;
            item.select();
            return true;
        }}""")
        if not found:
            log.warning("Sarasota FL: case type '%s' not found in dropdown", CASE_TYPE_LABEL)
            return []
        await page.wait_for_timeout(500)

        # Patch ClientState fields: the server uses these JSON blobs — not just the
        # visible input values — to determine what was selected.  The force-click
        # leaves CourtType.value="" and CaseType.checkedIndices=[], which the server
        # treats as "All". Fix both before submitting.
        await page.evaluate(r"""() => {
            const ctCs = document.querySelector('#ctl00_cphBody_rcbCourtType_ClientState');
            const ctState = JSON.parse(ctCs.value);
            ctState.value = "1";
            ctState.checkedIndices = [];
            ctCs.value = JSON.stringify(ctState);

            const caseCs = document.querySelector('#ctl00_cphBody_rcbCaseType_ClientState');
            const caseState = JSON.parse(caseCs.value);
            caseState.value = "10";
            caseState.checkedIndices = [4];
            caseCs.value = JSON.stringify(caseState);
        }""")

        # Date range via RadDatePicker (fill + Tab commits the value).
        date_from_str = start.strftime("%m/%d/%Y")
        date_to_str   = today.strftime("%m/%d/%Y")
        await page.fill(f"#{_DATE_FROM_ID}", date_from_str)
        await page.press(f"#{_DATE_FROM_ID}", "Tab")
        await page.wait_for_timeout(300)
        await page.fill(f"#{_DATE_TO_ID}", date_to_str)
        await page.press(f"#{_DATE_TO_ID}", "Tab")
        await page.wait_for_timeout(300)

        await page.click(_SEARCH_BTN)
        await page.wait_for_timeout(5_000)

        return await self._collect(page, start, today)

    # ------------------------------------------------------------------ #
    #  Results collection + pagination                                     #
    # ------------------------------------------------------------------ #

    async def _collect(self, page, start: date, today: date) -> list[Filing]:
        # Phase 1: paginate through results and collect all row metadata.
        # We must do this first because per-case fetches navigate away from the results.
        all_rows: list[dict] = []
        seen: set[str] = set()
        page_idx = 0
        stop_collecting = False

        while not stop_collecting:
            page_idx += 1
            rows = await self._extract_grid_rows(page)
            log.info("Sarasota FL: results page %d — %d rows", page_idx, len(rows))

            if not rows:
                break

            for row in rows:
                case_number = row.get("case_number")
                if not case_number or case_number in seen:
                    continue
                seen.add(case_number)
                filing_date = self._parse_date(row.get("filing_date_str"))
                row["_filing_date"] = filing_date
                if filing_date and filing_date < start:
                    log.info("Sarasota FL: reached cases older than window, stopping pagination")
                    stop_collecting = True
                    break
                if filing_date and start <= filing_date <= today:
                    all_rows.append(row)

            if stop_collecting or not await self._goto_next_page(page):
                break

        log.info("Sarasota FL: %d cases in window to fetch", len(all_rows))

        # Phase 2: fetch each case detail, download and OCR the complaint PDF.
        filings: list[Filing] = []
        for row in all_rows:
            filing = await self._fetch_case(page, row)
            if filing:
                filings.append(filing)
            await page.wait_for_timeout(int(_REQUEST_DELAY * 1000))

        return filings

    async def _extract_grid_rows(self, page) -> list[dict]:
        return await page.evaluate(r"""() => {
            const rows = [];
            const trs = document.querySelectorAll('table.rgMasterTable tbody tr');
            for (const tr of trs) {
                const cells = [...tr.querySelectorAll('td')];
                if (cells.length < 5) continue;
                // cell[0] = Case Number (link), cell[4] = Case File Date
                const anchor = cells[0].querySelector('a');
                if (!anchor) continue;
                const caseNum = (anchor.innerText || '').trim();
                if (!/\d{4}\s+[A-Z]+\s+\d+/.test(caseNum)) continue;
                const dateText = (cells[4].innerText || '').trim();
                rows.push({
                    case_number: caseNum,
                    filing_date_str: dateText || null,
                    case_url: anchor.href || null,
                    landlord: (cells[2].innerText || '').trim(),
                    defendant: (cells[3].innerText || '').trim(),
                });
            }
            return rows;
        }""")

    async def _goto_next_page(self, page) -> bool:
        # Fix the onclick="return false;" blocker on the next-page button.
        await page.evaluate(
            "() => { const b = document.querySelector('input.rgPageNext'); if (b) b.removeAttribute('onclick'); }"
        )
        btn = await page.query_selector("input.rgPageNext")
        if not btn:
            return False
        before = await self._first_case_number(page)
        try:
            await btn.click()
        except Exception:
            return False
        await page.wait_for_timeout(3_000)
        after = await self._first_case_number(page)
        return bool(after) and after != before

    async def _first_case_number(self, page) -> str | None:
        rows = await self._extract_grid_rows(page)
        return rows[0].get("case_number") if rows else None

    # ------------------------------------------------------------------ #
    #  Per-case fetch                                                      #
    # ------------------------------------------------------------------ #

    async def _fetch_case(self, page, row: dict) -> Filing | None:
        case_number = row["case_number"]
        filing_date = row.get("_filing_date") or self._parse_date(row.get("filing_date_str"))
        # Landlord and defendant are available directly from the grid row.
        landlord_raw = row.get("landlord", "")
        defendant_raw = row.get("defendant", "")

        # Navigate to CaseInfo by searching for the case number and clicking the result.
        # (direct.aspx?caseid= GUIDs are session-scoped and break on goto(); the
        # case-number search → result click is the only reliable path.)
        try:
            await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(800)
            await page.fill("#ctl00_cphBody_tbCaseNumber", case_number)
            await page.click(_SEARCH_BTN)
            await page.wait_for_timeout(3_000)
            case_link = await page.query_selector("table.rgMasterTable tbody tr td:first-child a")
            if not case_link:
                log.warning("Sarasota FL: case %s not found in case-number search", case_number)
                return None
            await case_link.click()
            await page.wait_for_timeout(2_500)
        except Exception as e:
            log.warning("Sarasota FL: could not open case %s: %s", case_number, e)
            return None

        # Parse landlord name from grid's "Primary Party" column (cell[2]).
        landlord = self._clean_party(landlord_raw)

        # Parse tenant name from grid's "Secondary Party" column (cell[3]).
        tenant = clean_tenant_name(self._clean_party(defendant_raw) or "") or "Unknown"

        # Find Complaint for Eviction PDF in the docket.
        complaint_href = await self._find_complaint_href(page)
        if not complaint_href:
            log.warning("Sarasota FL: no complaint PDF found in docket for %s", case_number)
            return None

        # Download and OCR the complaint PDF.
        address = await self._download_and_ocr(page, complaint_href, case_number)

        return Filing(
            case_number      = case_number,
            tenant_name      = tenant,
            property_address = address or "Unknown",
            landlord_name    = landlord or "Unknown",
            filing_date      = filing_date,
            court_date       = None,
            state            = STATE,
            county           = COUNTY,
            notice_type      = NOTICE_TYPE,
            source_url       = SOURCE_URL,
        )

    @staticmethod
    def _clean_party(raw: str) -> str | None:
        """Extract the first party name from a grid cell like 'SMITH, JOHN (Plaintiff)'."""
        if not raw:
            return None
        # Take only the first line (multiple parties possible).
        first_line = raw.split("\n")[0].strip()
        # Remove the trailing role label.
        name = re.sub(r"\s*\([^)]*\)\s*$", "", first_line).strip()
        return name or None

    async def _find_complaint_href(self, page) -> str | None:
        return await page.evaluate(r"""() => {
            const links = [...document.querySelectorAll('a[href*="ViewPDF"]')];
            for (const a of links) {
                const tr = a.closest('tr');
                if (tr && /complaint for eviction/i.test(tr.innerText)) return a.href;
            }
            return null;
        }""")

    async def _download_and_ocr(self, page, href: str, case_number: str) -> str | None:
        try:
            async with page.expect_download() as dl_info:
                await page.evaluate(f"window.location.href = '{href}'")
            download = await dl_info.value
            path = await download.path()
            with open(path, "rb") as f:
                pdf_bytes = f.read()
        except Exception as e:
            log.warning("Sarasota FL: PDF download failed for %s: %s", case_number, e)
            return None

        try:
            return self._ocr_address(pdf_bytes)
        except Exception as e:
            log.warning("Sarasota FL: OCR failed for %s: %s", case_number, e)
            return None

    # ------------------------------------------------------------------ #
    #  OCR + address parsing (pure / unit-testable)                       #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _ocr_address(pdf_bytes: bytes) -> str | None:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        mat = fitz.Matrix(300 / 72, 300 / 72)
        # Page 0 is often just the clerk's filing stamp; the complaint form
        # with the property address is on subsequent pages. Combine all pages.
        parts: list[str] = []
        for pg in doc:
            embedded = pg.get_text().strip()
            if embedded:
                parts.append(embedded)
            else:
                pix = pg.get_pixmap(matrix=mat)
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                parts.append(pytesseract.image_to_string(img, lang="eng"))
        addr = SarasotaScraper._parse_address("\n".join(parts))
        return _post_normalize(addr) if addr else None

    @staticmethod
    def _parse_address(ocr_text: str) -> str | None:
        # Pattern 1: attorney long-form (two-line: street then city/state/zip).
        m = _ADDR_RE_ATTORNEY.search(ocr_text)
        if m:
            street = m.group(1).strip().rstrip(",")
            city_state_zip = m.group(2).strip().rstrip(",")
            city_state_zip = re.sub(r"\bFlorida\b", "FL", city_state_zip, flags=re.IGNORECASE)
            return f"{street}, {city_state_zip}"

        # Pattern 2: Florida Bar pro-se form (single line, possibly in brackets).
        m2 = _ADDR_RE_PROSE.search(ocr_text)
        if m2:
            raw = m2.group(1).strip().strip("[]()").strip()
            addr = re.sub(r"\bFlorida\b", "FL", raw, flags=re.IGNORECASE)
            addr = re.sub(r"\s+", " ", addr).strip()
            return addr or None

        # Pattern 3: "property described as [follows:]" (attorney tenant-eviction form).
        m3 = _ADDR_RE_DESCRIBED.search(ocr_text)
        if m3:
            raw = m3.group(1).strip().strip("[]()").strip()
            addr = re.sub(r"\bFlorida\b", "FL", raw, flags=re.IGNORECASE)
            addr = re.sub(r"\s+", " ", addr).strip()
            return addr or None

        # Pattern 3B: multi-line "described as" with property name before street
        m3b = _ADDR_RE_DESCRIBED_ML.search(ocr_text)
        if m3b:
            street = re.sub(r"\s+", " ", m3b.group(1)).strip().rstrip(".,")
            city_zip = re.sub(r"\s+", " ", m3b.group(2)).strip().rstrip(".,")
            city_zip = re.sub(r"\bFlorida\b", "FL", city_zip, flags=re.IGNORECASE)
            return f"{street}, {city_zip}"

        # Pattern 4: checkbox form "PLAINTIFF VS. DEFENDANT\n<P NAME ADDR> | <D NAME ADDR>"
        # Defendant's address (= rental property) is the last FL-zip segment.
        m4 = _ADDR_RE_HEADER.search(ocr_text)
        if m4:
            line = m4.group(1)
            parts = line.split("|")
            for seg in reversed(parts):
                seg = seg.strip()
                if re.search(r"\bFL\s+\d{5}", seg, re.IGNORECASE):
                    addr_m = re.search(r"(\d+\s+\S.+FL\s+\d{5}(?:-\d{4})?)", seg, re.IGNORECASE)
                    if addr_m:
                        return re.sub(r"\s+", " ", addr_m.group(1)).strip().rstrip(".,")
                    return re.sub(r"\s+", " ", seg).strip() or None

        # Pattern 5A: pro-se form "<STREET>\nAddress\n\n<CITY, ZIP>\nCity, State, Zip Code"
        m5 = _ADDR_RE_LABEL.search(ocr_text)
        if m5:
            street = re.sub(r"\s+", " ", m5.group(1)).strip().rstrip(".,")
            city_zip = _normalize_city_zip(m5.group(2))
            return f"{street}, {city_zip}"

        # Pattern 5B: pro-se form "Address: <STREET>\n<CITY, ZIP>\nCity, State, Zip Code"
        m6 = _ADDR_RE_LABEL_INLINE.search(ocr_text)
        if m6:
            street = re.sub(r"\s+", " ", m6.group(1)).strip().rstrip(".,")
            city_zip = _normalize_city_zip(m6.group(2))
            return f"{street}, {city_zip}"

        return None

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_date(s: str | None) -> date | None:
        if not s:
            return None
        try:
            return datetime.strptime(s.strip(), "%m/%d/%Y").date()
        except ValueError:
            return None
