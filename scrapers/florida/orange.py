from __future__ import annotations

import io
import logging
import re
from datetime import date, datetime, timedelta

import httpx
import pdfplumber

from models.filing import Filing
from scrapers.base_scraper import BaseScraper
from scrapers.dates import court_today
from services.name_utils import clean_tenant_name

log = logging.getLogger(__name__)

# Portal: Orange County FL Clerk of Courts — myeclerk
# Search page exposes Case Type multiselect (Eviction = checkbox value "41")
# + DateFrom/DateTo text inputs (M/d/yy) + reCAPTCHA + Search button.
# Captcha is handled by Railway infra (BrightData + solver) at runtime.
PORTAL_URL       = "https://myeclerk.myorangeclerk.com/"
CASE_SEARCH_URL  = "https://myeclerk.myorangeclerk.com/Cases/search"
DOC_BASE_URL     = "https://myeclerk.myorangeclerk.com"
SOURCE_URL       = CASE_SEARCH_URL
STATE            = "FL"
COUNTY           = "Orange"
COURT_TIMEZONE   = "America/New_York"
NOTICE_TYPE      = "Residential Eviction"

EVICTION_CASE_TYPE_VALUE = "41"   # Confirmed via DOM inspection (June 2026)

# Regex for street-address recovery from Complaint PDFs.
# Florida complaints follow FL Statute 83 service-of-process formatting:
# tenant address typically appears as STREET + CITY, FL + ZIP on consecutive
# lines OR on one line separated by commas.
STREET_SUFFIX_REGEX = re.compile(
    r"\b\d{1,6}\s+[A-Z0-9][A-Z0-9 .'\-]*?\b"
    r"(?:STREET|ST|AVENUE|AVE|BOULEVARD|BLVD|ROAD|RD|DRIVE|DR|LANE|LN|"
    r"COURT|CT|CIRCLE|CIR|PLACE|PL|PARKWAY|PKWY|TERRACE|TER|TRAIL|TRL|"
    r"WAY|HIGHWAY|HWY|SQUARE|SQ|LOOP|ALLEY|ALY|ROUTE|RTE|RUN|PATH)\b"
    r"(?:\s+(?:APT|UNIT|#|STE|SUITE)\s*[A-Z0-9\-]+)?"
    r"(?:[,\s]+[A-Z][A-Z\s]*?,\s*FL\s*\d{5}(?:-\d{4})?)?",
    re.IGNORECASE,
)


class OrangeScraper(BaseScraper):
    """
    Scrapes Orange County FL Clerk of Courts for Residential Eviction filings.

    Portal: https://myeclerk.myorangeclerk.com/Cases/search

    Flow per run:
      1. Load /Cases/search
      2. Open Case Type multiselect → check "Eviction" (value=41)
      3. Fill DateFrom (today - lookback_days) and DateTo (today)
      4. (Captcha handled by Railway runtime infra)
      5. Click #caseSearch
      6. Iterate paginated case list — for each case number link:
         - Open case detail
         - Read Defendant name from Parties section
         - Click Complaint link in Docket Events → fetch PDF
         - Parse PDF for property address (STREET_SUFFIX_REGEX)
      7. Return Filing list
    """

    def __init__(self, lookback_days: int = 7, headless: bool = True):
        super().__init__(headless=headless)
        self.lookback_days = lookback_days

    async def scrape(self) -> list[Filing]:
        today = court_today(COURT_TIMEZONE)
        start = today - timedelta(days=self.lookback_days)
        filings: list[Filing] = []

        page = await self._launch_browser()
        try:
            log.info("Orange FL: loading case search page")
            await page.goto(CASE_SEARCH_URL, wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(3_000)

            filings = await self._run_search(page, start, today)
        except Exception as e:
            log.error("Orange FL: scrape failed: %s", e, exc_info=True)
        finally:
            await self._close_browser()

        log.info("Orange FL: %d filings found", len(filings))
        return filings

    # ------------------------------------------------------------------ #
    #  Search form                                                        #
    # ------------------------------------------------------------------ #

    async def _run_search(self, page, start: date, today: date) -> list[Filing]:
        # Cross-platform M/d/yy format (no leading zero on month/day, 2-digit year)
        start_str = f"{start.month}/{start.day}/{start.year % 100:02d}"
        end_str   = f"{today.month}/{today.day}/{today.year % 100:02d}"

        # Step 1 — open Case Type multiselect dropdown
        log.info("Orange FL: opening Case Type multiselect")
        await page.click("button.multiselect.dropdown-toggle")
        await page.wait_for_timeout(800)

        # Step 2 — check Eviction option (value=41)
        log.info("Orange FL: selecting Eviction case type")
        await page.click(f"input[type='checkbox'][value='{EVICTION_CASE_TYPE_VALUE}']")
        await page.wait_for_timeout(400)

        # Step 3 — close the multiselect by clicking the toggle again
        await page.click("button.multiselect.dropdown-toggle")
        await page.wait_for_timeout(400)

        # Step 4 — fill DateFrom and DateTo
        log.info("Orange FL: setting date range %s → %s", start_str, end_str)
        await page.fill("#DateFrom", start_str)
        await page.wait_for_timeout(300)
        await page.fill("#DateTo", end_str)
        await page.wait_for_timeout(300)

        # Step 5 — captcha handled by Railway infra (BrightData + solver)
        # Wait until search button enables OR a captcha solver toggles it.
        log.info("Orange FL: waiting for search button to enable")
        try:
            await page.wait_for_function(
                "() => { const b = document.querySelector('#caseSearch');"
                " return b && !b.disabled; }",
                timeout=60_000,
            )
        except Exception:
            log.warning("Orange FL: search button never enabled within 60s — captcha may be unsolved")
            return []

        # Step 6 — click Search
        log.info("Orange FL: clicking Search button")
        await page.click("#caseSearch")

        # Step 7 — wait for results table to render
        try:
            await page.wait_for_selector("table#caseList tbody tr", timeout=30_000)
        except Exception:
            log.warning("Orange FL: results table did not render after search")
            return []

        await page.wait_for_timeout(2_000)
        return await self._collect_all_pages(page, today)

    # ------------------------------------------------------------------ #
    #  Results table — paginate and collect                               #
    # ------------------------------------------------------------------ #

    async def _collect_all_pages(self, page, today: date) -> list[Filing]:
        filings: list[Filing] = []
        seen_cases: set[str] = set()
        page_idx = 1

        while True:
            log.info("Orange FL: collecting page %d", page_idx)
            page_filings = await self._collect_current_page(page, today, seen_cases)
            filings.extend(page_filings)

            # Find pagination Next link (anchor with text "Next")
            next_link = await page.query_selector("a[aria-controls='caseList']:has-text('Next')")
            if not next_link:
                break
            cls = (await next_link.get_attribute("class")) or ""
            if "disabled" in cls:
                break

            await next_link.click()
            await page.wait_for_timeout(2_500)
            page_idx += 1
            if page_idx > 100:
                log.warning("Orange FL: pagination safety stop at page 100")
                break

        return filings

    async def _collect_current_page(
        self, page, today: date, seen_cases: set[str]
    ) -> list[Filing]:
        filings: list[Filing] = []

        rows = await page.query_selector_all("table#caseList tbody tr")
        case_numbers: list[str] = []
        for row in rows:
            link = await row.query_selector("a")
            if not link:
                continue
            txt = (await link.inner_text()).strip()
            if txt and txt not in seen_cases:
                case_numbers.append(txt)
                seen_cases.add(txt)

        log.info("Orange FL: %d case numbers on this page", len(case_numbers))

        for case_number in case_numbers:
            try:
                filing = await self._process_case(page, case_number, today)
                if filing:
                    filings.append(filing)
                # Go back to the results list
                await page.go_back(wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_timeout(1_500)
            except Exception as e:
                log.warning("Orange FL: case %s failed: %s", case_number, e)

        return filings

    # ------------------------------------------------------------------ #
    #  Single case — open detail, read Parties, fetch Complaint PDF       #
    # ------------------------------------------------------------------ #

    async def _process_case(self, page, case_number: str, today: date) -> Filing | None:
        log.debug("Orange FL: processing %s", case_number)

        # Click the case-number link
        link = await page.query_selector(f"table#caseList a:has-text('{case_number}')")
        if not link:
            return None
        await link.click()
        await page.wait_for_load_state("domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(2_000)

        # Read Defendant + Plaintiff from Parties section
        defendant_name = await self._extract_defendant_name(page)
        plaintiff_name = await self._extract_plaintiff_name(page)

        # Find Complaint link in Docket Events table
        complaint_href = await self._find_complaint_href(page)

        property_address = "Unknown"
        if complaint_href:
            pdf_url = complaint_href
            if pdf_url.startswith("/"):
                pdf_url = DOC_BASE_URL + pdf_url
            property_address = await self._fetch_and_parse_complaint(pdf_url) or "Unknown"

        filing_date = today  # Refined below if filing date appears on detail page
        filing_date_text = await self._extract_filing_date(page)
        if filing_date_text:
            filing_date = filing_date_text

        return Filing(
            case_number      = case_number,
            tenant_name      = clean_tenant_name(defendant_name) or defendant_name or "Unknown",
            property_address = property_address,
            landlord_name    = plaintiff_name or "Unknown",
            filing_date      = filing_date,
            court_date       = None,
            state            = STATE,
            county           = COUNTY,
            notice_type      = NOTICE_TYPE,
            source_url       = page.url or SOURCE_URL,
        )

    async def _extract_defendant_name(self, page) -> str:
        """Defendant name lives in the Parties section. Try a few selectors."""
        candidates = [
            "tr:has(td:has-text('Defendant')) td:nth-child(2)",
            "tr:has(td:has-text('DEFENDANT')) td:nth-child(2)",
            "table.parties tr:has-text('Defendant') td:nth-child(2)",
        ]
        for sel in candidates:
            el = await page.query_selector(sel)
            if el:
                txt = (await el.inner_text()).strip()
                if txt:
                    return txt
        return ""

    async def _extract_plaintiff_name(self, page) -> str:
        candidates = [
            "tr:has(td:has-text('Plaintiff')) td:nth-child(2)",
            "tr:has(td:has-text('PLAINTIFF')) td:nth-child(2)",
        ]
        for sel in candidates:
            el = await page.query_selector(sel)
            if el:
                txt = (await el.inner_text()).strip()
                if txt:
                    return txt
        return ""

    async def _extract_filing_date(self, page) -> date | None:
        """Filing date often appears as 'Filed: MM/DD/YYYY' on case detail."""
        try:
            el = await page.query_selector("text=/Filed:?\\s*\\d{1,2}\\/\\d{1,2}\\/\\d{4}/")
            if el:
                raw = (await el.inner_text()).strip()
                m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", raw)
                if m:
                    mo, da, yr = int(m.group(1)), int(m.group(2)), int(m.group(3))
                    return date(yr, mo, da)
        except Exception:
            pass
        return None

    async def _find_complaint_href(self, page) -> str | None:
        """
        Locate the Complaint link in the Docket Events table.

        Confirmed structure (from live HTML):
          <td class="cdDocLink">
              <a class="noprint dDescription" href="/DocView/Doc?eCode=...">Complaint</a>
          </td>
        """
        candidates = [
            "td.cdDocLink a:has-text('Complaint')",
            "td.cdDocLink a.dDescription",
        ]
        for sel in candidates:
            el = await page.query_selector(sel)
            if el:
                href = await el.get_attribute("href")
                if href:
                    return href
        return None

    async def _fetch_and_parse_complaint(self, pdf_url: str) -> str | None:
        """Download Complaint PDF and extract the property address via regex."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(pdf_url)
                r.raise_for_status()
                pdf_bytes = r.content

            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                text_parts = []
                for p in pdf.pages:
                    t = p.extract_text() or ""
                    text_parts.append(t)
                full_text = "\n".join(text_parts)

            return self._parse_address_from_text(full_text)
        except Exception as e:
            log.warning("Orange FL: PDF fetch/parse failed for %s: %s", pdf_url, e)
            return None

    @staticmethod
    def _parse_address_from_text(text: str) -> str | None:
        if not text:
            return None
        # Normalize whitespace
        clean = re.sub(r"[ \t]+", " ", text)
        match = STREET_SUFFIX_REGEX.search(clean)
        if not match:
            return None
        return " ".join(match.group(0).split()).strip()
