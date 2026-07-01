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

from models.judgment import JudgmentRecord
from pipeline.gates import gate_address, gate_name
from scrapers.dates import court_today
from services.name_utils import clean_tenant_name

log = logging.getLogger(__name__)

# Sarasota County ClerkNet 3.0 — ISTS (eviction × judgment).
#
# Search approach: ClerkNet only filters by *filing* date, not judgment date.
# We search a wide filing window (90 days back) so cases that have since received
# a judgment are included, then check each case's docket for a fresh
# "JUDGMENT - RECORDED" entry. Only cases with a judgment dated within
# `judgment_lookback_days` of today are emitted.
#
# Judgment docket signature (observed July 2026):
#   "JUDGMENT - RECORDED - RECORDED (OR. / 2026XXXXXXX)"
#
# Address: eviction complaint PDF (scanned image), OCR via Tesseract.
# Same three regex patterns as sarasota.py (VDG scraper).

PORTAL_URL  = "https://secure.sarasotaclerk.com"
LANDING_URL = f"{PORTAL_URL}/AnonLanding.aspx"
SEARCH_URL  = f"{PORTAL_URL}/Search.aspx"

STATE  = "FL"
COUNTY = "Sarasota"

COURT_TYPE_LABEL = "Civil"
CASE_TYPE_LABEL  = "Evictions"

_COURT_TYPE_CTRL  = "ctl00_cphBody_rcbCourtType"
_CASE_TYPE_CTRL   = "ctl00_cphBody_rcbCaseType"
_COURT_TYPE_ARROW = "#ctl00_cphBody_rcbCourtType_Arrow"
_CASE_TYPE_ARROW  = "#ctl00_cphBody_rcbCaseType_Arrow"
_DATE_FROM_ID     = "ctl00_cphBody_rdStart_dateInput"
_DATE_TO_ID       = "ctl00_cphBody_rdEnd_dateInput"
_SEARCH_BTN       = "#ctl00_cphBody_bSearch_input"

# Telerik values for Civil/Evictions (verified July 2026)
_COURT_TYPE_VALUE   = "1"
_CASE_TYPE_VALUE    = "10"
_CASE_TYPE_INDEX    = 4

# Filing window: search cases filed within this window back from filing_end.
# Sarasota default judgments arrive 14-45 days after filing; 45 days is the practical max.
_FILING_LOOKBACK_DAYS = int(os.getenv("SARASOTA_ISTS_FILING_LOOKBACK", "30"))

# Judgment lookback: only emit judgments recorded within this many days of today.
_JUDGMENT_LOOKBACK_DAYS = int(os.getenv("SARASOTA_ISTS_JUDGMENT_LOOKBACK", "3"))

_REQUEST_DELAY = float(os.getenv("SARASOTA_ISTS_REQUEST_DELAY", "2.0"))

# ClerkNet silently returns zero rows for search windows wider than ~30 days.
# Chunk the filing lookback into sub-windows of this size.
_SEARCH_CHUNK_DAYS = 28

_TESSERACT_CMD = os.getenv(
    "TESSERACT_CMD", r"C:\Program Files\Tesseract-OCR\tesseract.exe"
)

# Docket entry text that signals a recorded final judgment (case-insensitive).
_JUDGMENT_TEXT = "JUDGMENT - RECORDED"

# Address regexes — same three variants as the VDG eviction scraper, plus a
# fourth for the checkbox-style form where plaintiff+defendant appear on one line:
#   "PLAINTIFF VS. DEFENDANT\n<NAME ADDR> | <NAME ADDR>"
# We extract the rightmost FL-zip-containing segment (the defendant's portion).
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
# The defendant's address is the last FL-zip segment on the header line.
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


class SarasotaISTSScraper:
    """
    Scrapes Sarasota County ClerkNet 3.0 for eviction judgments (ISTS).

    Searches Civil/Evictions with a wide filing-date window, then per-case checks
    the docket for a recent "JUDGMENT - RECORDED" entry. On match: downloads the
    Complaint for Eviction PDF, OCRs it, and extracts the property address.
    Only cases where gate_name + gate_address pass are returned.
    """

    def __init__(
        self,
        filing_lookback_days: int = _FILING_LOOKBACK_DAYS,
        judgment_lookback_days: int = _JUDGMENT_LOOKBACK_DAYS,
        headless: bool = True,
    ):
        self.filing_lookback_days = filing_lookback_days
        self.judgment_lookback_days = judgment_lookback_days
        self.headless = headless
        self.last_error: Optional[str] = None
        self._playwright = None
        self._browser = None
        pytesseract.pytesseract.tesseract_cmd = _TESSERACT_CMD

    async def scrape(self) -> list[JudgmentRecord]:
        self.last_error = None
        today = court_today("America/New_York")
        # Search cases filed in the window [today - lookback, today - 14].
        # Cases filed less than 14 days ago can't have judgments yet.
        filing_end = today - timedelta(days=14)
        filing_start = filing_end - timedelta(days=self.filing_lookback_days)
        judgment_cutoff = today - timedelta(days=self.judgment_lookback_days)

        page = await self._launch_browser()
        records: list[JudgmentRecord] = []
        try:
            log.info("Sarasota ISTS: establishing anonymous session")
            await page.goto(LANDING_URL, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(1_500)
            await page.click("#cphBody_bAgree", timeout=10_000)
            await page.wait_for_timeout(1_500)

            records = await self._search(page, filing_start, filing_end, judgment_cutoff, today)
        except Exception as e:
            self.last_error = str(e)
            log.error("Sarasota ISTS: scrape failed: %s", e, exc_info=True)
        finally:
            await self._close_browser()

        unique = {r.case_number: r for r in records}
        result = list(unique.values())
        log.info("Sarasota ISTS: %d judgment records found", len(result))
        return result

    # ------------------------------------------------------------------ #
    #  Browser                                                             #
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

    async def _close_browser(self):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self._browser = None
        self._playwright = None

    # ------------------------------------------------------------------ #
    #  Search                                                              #
    # ------------------------------------------------------------------ #

    async def _search(
        self, page, filing_start: date, filing_end: date,
        judgment_cutoff: date, today: date,
    ) -> list[JudgmentRecord]:
        # ClerkNet silently returns zero rows for windows wider than ~30 days.
        # Chunk the filing window and union the results.
        all_rows: list[dict] = []
        seen: set[str] = set()
        chunk_start = filing_start
        chunk_num = 0

        while chunk_start <= filing_end:
            chunk_end = min(chunk_start + timedelta(days=_SEARCH_CHUNK_DAYS - 1), filing_end)
            chunk_num += 1
            log.info(
                "Sarasota ISTS: filing chunk %d: %s to %s",
                chunk_num, chunk_start, chunk_end,
            )
            chunk_rows = await self._search_chunk(page, chunk_start, chunk_end)
            for row in chunk_rows:
                cn = row.get("case_number")
                if cn and cn not in seen:
                    seen.add(cn)
                    all_rows.append(row)
            chunk_start = chunk_end + timedelta(days=1)

        log.info("Sarasota ISTS: %d unique cases across %d chunks", len(all_rows), chunk_num)
        return await self._check_cases(page, all_rows, judgment_cutoff, today)

    async def _search_chunk(self, page, chunk_start: date, chunk_end: date) -> list[dict]:
        """Run a single Search.aspx query for one filing-date chunk."""
        await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(1_500)

        await page.click(_COURT_TYPE_ARROW)
        await page.wait_for_timeout(600)
        await page.click(
            f"#ctl00_cphBody_rcbCourtType_DropDown li.rcbItem:has-text('{COURT_TYPE_LABEL}')",
            force=True, timeout=5_000,
        )
        await page.wait_for_timeout(3_000)

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
            log.warning("Sarasota ISTS: case type '%s' not found in chunk search", CASE_TYPE_LABEL)
            return []
        await page.wait_for_timeout(500)

        await page.evaluate(f"""() => {{
            const ctCs = document.querySelector('#ctl00_cphBody_rcbCourtType_ClientState');
            const ctState = JSON.parse(ctCs.value);
            ctState.value = "{_COURT_TYPE_VALUE}";
            ctState.checkedIndices = [];
            ctCs.value = JSON.stringify(ctState);

            const caseCs = document.querySelector('#ctl00_cphBody_rcbCaseType_ClientState');
            const caseState = JSON.parse(caseCs.value);
            caseState.value = "{_CASE_TYPE_VALUE}";
            caseState.checkedIndices = [{_CASE_TYPE_INDEX}];
            caseCs.value = JSON.stringify(caseState);
        }}""")

        await page.fill(f"#{_DATE_FROM_ID}", chunk_start.strftime("%m/%d/%Y"))
        await page.press(f"#{_DATE_FROM_ID}", "Tab")
        await page.wait_for_timeout(300)
        await page.fill(f"#{_DATE_TO_ID}", chunk_end.strftime("%m/%d/%Y"))
        await page.press(f"#{_DATE_TO_ID}", "Tab")
        await page.wait_for_timeout(300)

        await page.click(_SEARCH_BTN)
        await page.wait_for_timeout(5_000)

        return await self._paginate_rows(page)

    # ------------------------------------------------------------------ #
    #  Pagination helpers                                                  #
    # ------------------------------------------------------------------ #

    async def _paginate_rows(self, page) -> list[dict]:
        """Paginate the current results grid and return all rows."""
        all_rows: list[dict] = []
        seen: set[str] = set()
        page_idx = 0
        while True:
            page_idx += 1
            rows = await self._extract_grid_rows(page)
            log.info("Sarasota ISTS: grid page %d — %d rows", page_idx, len(rows))
            if not rows:
                break
            for row in rows:
                cn = row.get("case_number")
                if cn and cn not in seen:
                    seen.add(cn)
                    all_rows.append(row)
            if not await self._goto_next_page(page):
                break
        return all_rows

    async def _check_cases(
        self, page, all_rows: list[dict], judgment_cutoff: date, today: date,
    ) -> list[JudgmentRecord]:
        """Per-case docket check + PDF OCR for cases that have a fresh judgment."""
        log.info("Sarasota ISTS: %d cases to check for judgments", len(all_rows))
        records: list[JudgmentRecord] = []
        for row in all_rows:
            record = await self._check_case(page, row, judgment_cutoff, today)
            if record:
                records.append(record)
            await page.wait_for_timeout(int(_REQUEST_DELAY * 1000))
        return records

    async def _extract_grid_rows(self, page) -> list[dict]:
        return await page.evaluate(r"""() => {
            const rows = [];
            const trs = document.querySelectorAll('table.rgMasterTable tbody tr');
            for (const tr of trs) {
                const cells = [...tr.querySelectorAll('td')];
                if (cells.length < 5) continue;
                const anchor = cells[0].querySelector('a');
                if (!anchor) continue;
                const caseNum = (anchor.innerText || '').trim();
                if (!caseNum) continue;
                rows.push({
                    case_number: caseNum,
                    filing_date_str: (cells[4].innerText || '').trim() || null,
                    plaintiff: (cells[2].innerText || '').trim(),
                    defendant: (cells[3].innerText || '').trim(),
                });
            }
            return rows;
        }""")

    async def _goto_next_page(self, page) -> bool:
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
    #  Per-case: docket check + PDF address                               #
    # ------------------------------------------------------------------ #

    async def _check_case(
        self, page, row: dict, judgment_cutoff: date, today: date,
    ) -> JudgmentRecord | None:
        case_number = row["case_number"]
        plaintiff_raw = row.get("plaintiff", "")
        defendant_raw = row.get("defendant", "")

        try:
            await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(800)
            await page.fill("#ctl00_cphBody_tbCaseNumber", case_number)
            await page.click(_SEARCH_BTN)
            await page.wait_for_timeout(3_000)
            case_link = await page.query_selector("table.rgMasterTable tbody tr td:first-child a")
            if not case_link:
                log.warning("Sarasota ISTS: case %s not found in case-number search", case_number)
                return None
            await case_link.click()
            await page.wait_for_timeout(2_500)
        except Exception as e:
            log.warning("Sarasota ISTS: could not open case %s: %s", case_number, e)
            return None

        # Read docket rows from the dedicated docket table (id ends in rgDocket_ctl00).
        # Column layout: cell[0]=checkbox, cell[1]=Docket Date, cell[2]=DIN,
        #                cell[3]=Description, cell[4]=Pages, cell[5]=Image link.
        docket_rows = await page.evaluate(r"""() => {
            const rows = [];
            const table = document.querySelector('table[id*="rgDocket"]');
            if (!table) return rows;
            for (const tr of table.querySelectorAll('tbody tr')) {
                const cells = [...tr.querySelectorAll('td')];
                if (cells.length < 4) continue;
                rows.push({
                    date_str: (cells[1].innerText || '').trim(),
                    text: (cells[3].innerText || '').trim(),
                    has_pdf: !!tr.querySelector('a[href*="ViewPDF"]'),
                    pdf_href: tr.querySelector('a[href*="ViewPDF"]') ? tr.querySelector('a[href*="ViewPDF"]').href : null,
                });
            }
            return rows;
        }""")

        # Find a JUDGMENT - RECORDED entry within the judgment lookback window.
        judgment_date = None
        for dr in docket_rows:
            if _JUDGMENT_TEXT in dr["text"].upper():
                jdate = _parse_date(dr["date_str"])
                if jdate and judgment_cutoff <= jdate <= today:
                    judgment_date = jdate
                    break

        if not judgment_date:
            return None

        log.info("Sarasota ISTS: %s — judgment on %s", case_number, judgment_date)

        # Name gates on the grid-level defendant name (no need to open PDF first).
        defendant_name = _clean_party(defendant_raw) or ""
        tenant = clean_tenant_name(defendant_name) or ""
        if not gate_name(tenant):
            log.info("Sarasota ISTS: %s — gate_name failed (%s)", case_number, tenant)
            return None

        plaintiff = _clean_party(plaintiff_raw)

        # Download Complaint for Eviction PDF and OCR the property address.
        complaint_href = await self._find_complaint_href(page)
        if not complaint_href:
            log.warning("Sarasota ISTS: %s — no complaint PDF in docket", case_number)
            return None

        address = await self._download_and_ocr(page, complaint_href, case_number)
        if not gate_address(address or ""):
            log.info("Sarasota ISTS: %s — gate_address failed (%s)", case_number, address)
            return None

        return JudgmentRecord(
            case_number          = case_number,
            defendant_name       = tenant,
            property_address     = address,
            plaintiff_name       = plaintiff,
            state                = STATE,
            county               = COUNTY,
            judgment_date        = judgment_date,
            judgment_in_favor_of = plaintiff,
            judgment_against     = tenant,
            disposition_desc     = "FINAL JUDGMENT - POSSESSION",
            disposition_date     = judgment_date,
            source_url           = SEARCH_URL,
        )

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
                await page.evaluate("(href) => { window.location.href = href; }", href)
            download = await dl_info.value
            path = await download.path()
            with open(path, "rb") as f:
                pdf_bytes = f.read()
        except Exception as e:
            log.warning("Sarasota ISTS: PDF download failed for %s: %s", case_number, e)
            return None

        try:
            return _ocr_address(pdf_bytes)
        except Exception as e:
            log.warning("Sarasota ISTS: OCR failed for %s: %s", case_number, e)
            return None


# ------------------------------------------------------------------ #
#  Pure helpers (unit-testable)                                        #
# ------------------------------------------------------------------ #

def _ocr_address(pdf_bytes: bytes) -> str | None:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    mat = fitz.Matrix(300 / 72, 300 / 72)
    # Page 0 is often just the clerk's filing stamp; the complaint form
    # with the property address is on subsequent pages. Try embedded text
    # first (fast), fall back to OCR. Combine all pages so regexes have
    # full context.
    all_text_parts: list[str] = []
    for pg in doc:
        embedded = pg.get_text().strip()
        if embedded:
            all_text_parts.append(embedded)
        else:
            pix = pg.get_pixmap(matrix=mat)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            all_text_parts.append(pytesseract.image_to_string(img, lang="eng"))
    full_text = "\n".join(all_text_parts)
    addr = _parse_address(full_text)
    return _post_normalize(addr) if addr else None


def _post_normalize(addr: str) -> str:
    """Final cleanup on any extracted address: fix FL. / FL spacing issues."""
    addr = re.sub(r"\bFL\.?\s*(\d{5})", r"FL \1", addr)
    addr = re.sub(r"\s+", " ", addr).strip()
    return addr


def _parse_address(ocr_text: str) -> str | None:
    m = _ADDR_RE_ATTORNEY.search(ocr_text)
    if m:
        street = m.group(1).strip().rstrip(",")
        city_state_zip = m.group(2).strip().rstrip(",")
        city_state_zip = re.sub(r"\bFlorida\b", "FL", city_state_zip, flags=re.IGNORECASE)
        return f"{street}, {city_state_zip}"

    m2 = _ADDR_RE_PROSE.search(ocr_text)
    if m2:
        raw = m2.group(1).strip().strip("[]()").strip()
        addr = re.sub(r"\bFlorida\b", "FL", raw, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", addr).strip() or None

    m3 = _ADDR_RE_DESCRIBED.search(ocr_text)
    if m3:
        raw = m3.group(1).strip().strip("[]()").strip()
        addr = re.sub(r"\bFlorida\b", "FL", raw, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", addr).strip() or None

    # Pattern 3B: multi-line "described as" with property name before street
    m3b = _ADDR_RE_DESCRIBED_ML.search(ocr_text)
    if m3b:
        street = re.sub(r"\s+", " ", m3b.group(1)).strip().rstrip(".,")
        city_zip = re.sub(r"\s+", " ", m3b.group(2)).strip().rstrip(".,")
        city_zip = re.sub(r"\bFlorida\b", "FL", city_zip, flags=re.IGNORECASE)
        return f"{street}, {city_zip}"

    # Checkbox form: "PLAINTIFF VS. DEFENDANT\n<P NAME ADDR> | <D NAME ADDR>"
    # The defendant portion is after the last pipe (|) separator.
    m4 = _ADDR_RE_HEADER.search(ocr_text)
    if m4:
        line = m4.group(1)
        parts = line.split("|")
        # Defendant's segment is the last part containing a FL zip
        for seg in reversed(parts):
            seg = seg.strip()
            if re.search(r"\bFL\s+\d{5}", seg, re.IGNORECASE):
                # Extract just the address portion: digits start = street begins
                addr_m = re.search(r"(\d+\s+\S.+FL\s+\d{5}(?:-\d{4})?)", seg, re.IGNORECASE)
                if addr_m:
                    addr = re.sub(r"\s+", " ", addr_m.group(1)).strip().rstrip(".,")
                    return addr
                addr = re.sub(r"\s+", " ", seg).strip()
                return addr or None

    # Pro-se form variant A: "<STREET>\nAddress\n\n<CITY, ZIP>\nCity, State, Zip Code"
    m5 = _ADDR_RE_LABEL.search(ocr_text)
    if m5:
        street = re.sub(r"\s+", " ", m5.group(1)).strip().rstrip(".,")
        city_zip = _normalize_city_zip(m5.group(2))
        return f"{street}, {city_zip}"

    # Pro-se form variant B: "Address: <STREET>\n<CITY, ZIP>\nCity, State, Zip Code"
    m6 = _ADDR_RE_LABEL_INLINE.search(ocr_text)
    if m6:
        street = re.sub(r"\s+", " ", m6.group(1)).strip().rstrip(".,")
        city_zip = _normalize_city_zip(m6.group(2))
        return f"{street}, {city_zip}"

    return None


def _normalize_city_zip(raw: str) -> str:
    """Normalize OCR city/state/zip: fix common FL misreads, collapse whitespace."""
    s = re.sub(r"\s+", " ", raw).strip().rstrip(".,")
    # Fix OCR misreads of "FL": Ft, Fu, Fi, F|, Fl → FL
    s = re.sub(r"\bF[tTuUiI|l]\.?\b", "FL", s)
    s = re.sub(r"\bFlorida\b", "FL", s, flags=re.IGNORECASE)
    # Ensure space between FL and zip if missing: "FL.34232" → "FL 34232"
    s = re.sub(r"\bFL\.?\s*(\d{5})", r"FL \1", s)
    return s


def _clean_party(raw: str) -> str | None:
    if not raw:
        return None
    first_line = raw.split("\n")[0].strip()
    name = re.sub(r"\s*\([^)]*\)\s*$", "", first_line).strip()
    return name or None


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), "%m/%d/%Y").date()
    except ValueError:
        return None
