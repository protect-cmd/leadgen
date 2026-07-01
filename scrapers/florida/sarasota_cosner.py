from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, timedelta
from typing import Optional

import fitz
from playwright.async_api import async_playwright

from models.cosner import CosnerFiling
from scrapers.dates import court_today

log = logging.getLogger(__name__)

# Sarasota County ClerkNet 3.0 — Small Claims debt filings for Cosner Drake.
#
# Portal mechanics are identical to sarasota.py (VDG eviction scraper):
#   - Court Type: Civil (value=1)
#   - Case Type: Small Claims (value=21, index=9)
#   - Telerik ClientState patches required before submit (same quirk as evictions)
#
# Address source: "SUMMONS ISSUED" docket PDF (not the Statement of Claim).
#   The Summons has the defendant's served address in direct-text format:
#       DEFENDANT NAME
#       STREET ADDRESS
#       CITY, FL ZIP
#       DEFENDANT
#   No OCR needed — these are e-filed PDFs with embedded text.
#
# Debt amount: extracted from Statement of Claim PDF via regex on embedded text.

PORTAL_URL  = "https://secure.sarasotaclerk.com"
LANDING_URL = f"{PORTAL_URL}/AnonLanding.aspx"
SEARCH_URL  = f"{PORTAL_URL}/Search.aspx"

STATE  = "FL"
COUNTY = "Sarasota"

COURT_TYPE_LABEL = "Civil"
CASE_TYPE_LABEL  = "Small Claims"

_COURT_TYPE_CTRL  = "ctl00_cphBody_rcbCourtType"
_CASE_TYPE_CTRL   = "ctl00_cphBody_rcbCaseType"
_COURT_TYPE_ARROW = "#ctl00_cphBody_rcbCourtType_Arrow"
_CASE_TYPE_ARROW  = "#ctl00_cphBody_rcbCaseType_Arrow"
_DATE_FROM_ID     = "ctl00_cphBody_rdStart_dateInput"
_DATE_TO_ID       = "ctl00_cphBody_rdEnd_dateInput"
_SEARCH_BTN       = "#ctl00_cphBody_bSearch_input"

# Telerik values verified July 2026:  Civil=1, Small Claims value=21, index=9
_COURT_TYPE_VALUE    = "1"
_CASE_TYPE_VALUE     = "21"
_CASE_TYPE_INDEX     = 9

FL_ANSWER_WINDOW_DAYS = 30

_REQUEST_DELAY = float(os.getenv("SARASOTA_CD_REQUEST_DELAY", "2.0"))

# Regex for defendant address block in Summons PDF text (embedded, not OCR).
# The summons lists defendant below "- vs -" in this format:
#   NAME
#   STREET
#   CITY, FL ZIP[-4]
#   DEFENDANT
_SUMMONS_ADDR_RE = re.compile(
    r"-\s*vs\s*-.*?(?:\n[^\n]*){1,5}\n([^\n]+)\n(\d+[^\n]+)\n([^\n]+,\s*FL\s+\d{5}(?:-\d{4})?)\s*\nDEFENDANT",
    re.IGNORECASE | re.DOTALL,
)

# Regex for debt amount in Statement of Claim.
# Examples: "owes Plaintiff the principal balance of $1,696.17"
#           "judgment against Defendant in the amount of $3,450.00"
_AMOUNT_RE = re.compile(
    r"(?:principal\s+balance|amount\s+of)\s+\$\s*([\d,]+\.?\d*)",
    re.IGNORECASE,
)


class SarasotaCosnerScraper:
    """
    Scrapes Sarasota County Small Claims filings (debt, pre-judgment) for Cosner Drake.

    Returns CosnerFiling objects. Address is extracted from the Summons Issued PDF;
    debt amount from the Statement of Claim PDF. Both are e-filed embedded-text PDFs.
    """

    def __init__(self, lookback_days: int = 2, headless: bool = True):
        self.lookback_days = lookback_days
        self.headless = headless
        self.last_error: Optional[str] = None
        self._playwright = None
        self._browser = None

    async def scrape(self) -> list[CosnerFiling]:
        self.last_error = None
        today = court_today("America/New_York")
        start = today - timedelta(days=self.lookback_days)

        page = await self._launch_browser()
        filings: list[CosnerFiling] = []
        try:
            log.info("Sarasota Cosner: establishing anonymous session")
            await page.goto(LANDING_URL, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(1_500)
            await page.click("#cphBody_bAgree", timeout=10_000)
            await page.wait_for_timeout(1_500)

            filings = await self._search(page, start, today)
        except Exception as e:
            self.last_error = str(e)
            log.error("Sarasota Cosner: scrape failed: %s", e, exc_info=True)
        finally:
            await self._close_browser()

        unique = {f.case_number: f for f in filings}
        result = list(unique.values())
        log.info("Sarasota Cosner: %d filings found", len(result))
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

    async def _search(self, page, start: date, today: date) -> list[CosnerFiling]:
        await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(1_500)

        # Court Type: open arrow → force-click "Civil" li (fires AutoPostBack AJAX).
        await page.click(_COURT_TYPE_ARROW)
        await page.wait_for_timeout(600)
        await page.click(
            f"#ctl00_cphBody_rcbCourtType_DropDown li.rcbItem:has-text('{COURT_TYPE_LABEL}')",
            force=True,
            timeout=5_000,
        )
        await page.wait_for_timeout(3_000)

        # Case Type: lazy-loaded — open arrow first, then select via Telerik JS API.
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
            log.warning("Sarasota Cosner: case type '%s' not found", CASE_TYPE_LABEL)
            return []
        await page.wait_for_timeout(500)

        # Patch ClientState: server reads these JSON blobs, not the visible inputs.
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

    async def _collect(self, page, start: date, today: date) -> list[CosnerFiling]:
        # Phase 1: paginate and snapshot all result rows before navigating away.
        all_rows: list[dict] = []
        seen: set[str] = set()
        page_idx = 0
        stop_collecting = False

        while not stop_collecting:
            page_idx += 1
            rows = await self._extract_grid_rows(page)
            log.info("Sarasota Cosner: results page %d — %d rows", page_idx, len(rows))
            if not rows:
                break

            for row in rows:
                case_number = row.get("case_number")
                if not case_number or case_number in seen:
                    continue
                seen.add(case_number)
                filing_date = _parse_date(row.get("filing_date_str"))
                row["_filing_date"] = filing_date
                if filing_date and filing_date < start:
                    stop_collecting = True
                    break
                if filing_date and start <= filing_date <= today:
                    all_rows.append(row)

            if stop_collecting or not await self._goto_next_page(page):
                break

        log.info("Sarasota Cosner: %d cases in window to fetch", len(all_rows))

        # Phase 2: per-case fetch (navigate to CaseInfo, download PDFs, extract data).
        filings: list[CosnerFiling] = []
        for row in all_rows:
            filing = await self._fetch_case(page, row)
            if filing:
                filings.append(filing)
            await page.wait_for_timeout(int(_REQUEST_DELAY * 1000))

        return filings

    async def _extract_grid_rows(self, page) -> list[dict]:
        return await page.evaluate("""() => {
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
                    creditor: (cells[2].innerText || '').trim(),
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
    #  Per-case fetch                                                      #
    # ------------------------------------------------------------------ #

    async def _fetch_case(self, page, row: dict) -> CosnerFiling | None:
        case_number = row["case_number"]
        filing_date = row.get("_filing_date") or _parse_date(row.get("filing_date_str"))
        creditor = _clean_party(row.get("creditor", ""))
        defendant_name = _clean_party(row.get("defendant", ""))

        try:
            await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(800)
            await page.fill("#ctl00_cphBody_tbCaseNumber", case_number)
            await page.click(_SEARCH_BTN)
            await page.wait_for_timeout(3_000)
            case_link = await page.query_selector("table.rgMasterTable tbody tr td:first-child a")
            if not case_link:
                log.warning("Sarasota Cosner: case %s not found in search", case_number)
                return None
            await case_link.click()
            await page.wait_for_timeout(2_500)
        except Exception as e:
            log.warning("Sarasota Cosner: could not open case %s: %s", case_number, e)
            return None

        pdf_links = await page.evaluate("""() => {
            const links = [...document.querySelectorAll('a[href*="ViewPDF"]')];
            return links.map(a => {
                const tr = a.closest('tr');
                return { href: a.href, row_text: tr ? tr.innerText.trim() : '' };
            });
        }""")

        # Extract defendant address from Summons Issued PDF.
        defendant_address = await self._extract_address_from_summons(page, pdf_links, case_number)

        # Extract debt amount from Statement of Claim PDF.
        debt_amount, amount_kind = await self._extract_amount_from_claim(page, pdf_links, case_number)

        if not defendant_address:
            log.info("Sarasota Cosner: %s — no address extracted", case_number)

        answer_deadline = (filing_date + timedelta(days=FL_ANSWER_WINDOW_DAYS)) if filing_date else None

        return CosnerFiling(
            case_number      = case_number,
            defendant_name   = defendant_name or "Unknown",
            defendant_address= defendant_address or "Unknown",
            creditor_name    = creditor,
            state            = STATE,
            county           = COUNTY,
            filing_date      = filing_date,
            answer_deadline  = answer_deadline,
            debt_amount      = debt_amount,
            amount_kind      = amount_kind,
            source_url       = SEARCH_URL,
        )

    async def _extract_address_from_summons(
        self, page, pdf_links: list[dict], case_number: str
    ) -> str | None:
        summons_hrefs = [l["href"] for l in pdf_links if "SUMMONS ISSUED" in l["row_text"].upper()]
        if not summons_hrefs:
            log.info("Sarasota Cosner: %s — no SUMMONS ISSUED in docket", case_number)
            return None

        # Try each summons PDF; return first successful address extraction.
        # Cases with multiple defendants (e.g., individual + LLC) have multiple summons.
        for href in summons_hrefs:
            try:
                pdf_bytes = await self._download_pdf(page, href, case_number)
            except Exception as e:
                log.warning("Sarasota Cosner: %s — summons download failed: %s", case_number, e)
                continue
            addr = _parse_summons_address(pdf_bytes)
            if addr:
                return addr

        return None

    async def _extract_amount_from_claim(
        self, page, pdf_links: list[dict], case_number: str
    ) -> tuple[float | None, str | None]:
        claim_href = None
        for l in pdf_links:
            if "STATEMENT OF CLAIM" in l["row_text"].upper():
                claim_href = l["href"]
                break
        if not claim_href:
            return None, None

        try:
            pdf_bytes = await self._download_pdf(page, claim_href, case_number)
        except Exception as e:
            log.warning("Sarasota Cosner: %s — claim download failed: %s", case_number, e)
            return None, None

        return _parse_claim_amount(pdf_bytes)

    async def _download_pdf(self, page, href: str, case_number: str) -> bytes:
        async with page.expect_download() as dl_info:
            await page.evaluate("(href) => { window.location.href = href; }", href)
        download = await dl_info.value
        path = await download.path()
        with open(path, "rb") as f:
            return f.read()


# ------------------------------------------------------------------ #
#  Pure helpers (unit-testable)                                        #
# ------------------------------------------------------------------ #

def _parse_summons_address(pdf_bytes: bytes) -> str | None:
    """Extract defendant address from an embedded-text Summons PDF."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text = ""
    for pg in doc:
        text += pg.get_text()

    m = _SUMMONS_ADDR_RE.search(text)
    if m:
        street = m.group(2).strip()
        city_state_zip = m.group(3).strip()
        return f"{street}, {city_state_zip}"

    return None


def _parse_claim_amount(pdf_bytes: bytes) -> tuple[float | None, str | None]:
    """Extract principal debt amount from Statement of Claim embedded text."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text = ""
    for pg in doc:
        text += pg.get_text()

    m = _AMOUNT_RE.search(text)
    if m:
        raw = m.group(1).replace(",", "")
        try:
            return float(raw), "principal"
        except ValueError:
            pass
    return None, None


def _clean_party(raw: str) -> str | None:
    """Extract first party name from grid cell like 'MIDLAND CREDIT MANAGEMENT INC (Plaintiff)'."""
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
