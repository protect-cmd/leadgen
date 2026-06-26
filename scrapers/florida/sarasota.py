from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Optional

from playwright.async_api import async_playwright

from models.filing import Filing
from scrapers.base_scraper import BaseScraper
from scrapers.dates import court_today
from services.name_utils import clean_tenant_name

log = logging.getLogger(__name__)

# Sarasota County Clerk "ClerkNet" portal. Anonymous public access — no login,
# no captcha. The Civil search is date-range enumerable, so we search Civil for
# the lookback window and keep rows whose Case Type names an eviction.
#
# Confirmed from live inspection (June 2026):
#   - Landing -> "Click Here For General" (anonymous) -> disclaimer "Agree".
#   - Search at /Search.aspx is a Telerik (RadControls) form. Driven via the
#     Telerik client API ($find):
#       Court Type combo : ctl00_cphBody_rcbCourtType  (pick "Civil")
#       Start date picker: ctl00_cphBody_rdStart       (set_selectedDate)
#       End date picker  : ctl00_cphBody_rdEnd
#       Search button    : ctl00_cphBody_btnSearch
#   - Results grid (ctl00_cphBody_rgCaseList) columns:
#       Case Number | Case Status | Primary Party (plaintiff/landlord) |
#       Secondary Party (defendant/tenant) | Case File Date | Case Type.
#     Max page size is 50; paginate with the "Next Page" pager button.
#
# The list has no property address (a per-case detail lookup would be a future
# enhancement), so filings use property_address="Unknown".
PORTAL_URL     = "https://secure.sarasotaclerk.com/"
SEARCH_URL     = "https://secure.sarasotaclerk.com/Search.aspx"
SOURCE_URL     = PORTAL_URL
STATE          = "FL"
COUNTY         = "Sarasota"
COURT_TIMEZONE = "America/New_York"
NOTICE_TYPE    = "Eviction"

PAGE_SIZE = 50

_CASE_NUM_RE = re.compile(r"^\d{4}\s+[A-Z]{2,3}\s+\d+")
_PARTY_TAG_RE = re.compile(r"\s*\((?:Plaintiff|Defendant|Petitioner|Respondent)[^)]*\)", re.IGNORECASE)
_FILE_DATE_RE = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{4})\b")

# Pull the cell text out of the live RadGrid data rows.
_GRID_ROWS_JS = r"""() => {
  const grid = document.querySelector('#ctl00_cphBody_rgCaseList');
  if (!grid) return [];
  return [...grid.querySelectorAll('tr.rgRow, tr.rgAltRow')]
    .map(r => [...r.cells].map(c => c.innerText.trim().replace(/\s+/g, ' ')));
}"""

_PAGER_TEXT_JS = r"""() => {
  const m = document.body.innerText.match(/(\d+)\s+items?\s+in\s+(\d+)\s+pages?/i);
  return m ? { items: +m[1], pages: +m[2] } : null;
}"""


class SarasotaScraper(BaseScraper):
    """
    Scrapes Sarasota County ClerkNet for eviction filings.

    Searches Civil for the lookback window (anonymous, no captcha), paginates
    the results, and keeps rows whose Case Type names an eviction.
    """

    def __init__(self, lookback_days: int = 2, headless: bool = True, max_pages: int = 40):
        super().__init__(headless=headless)
        self.lookback_days = lookback_days
        self.max_pages = max_pages
        self.last_error: Optional[str] = None

    async def _launch_browser(self):
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await self._browser.new_context(
            ignore_https_errors=True,
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

    async def scrape(self) -> list[Filing]:
        self.last_error = None
        today = court_today(COURT_TIMEZONE)
        start = today - timedelta(days=self.lookback_days)
        filings: list[Filing] = []

        page = await self._launch_browser()
        try:
            await self._anonymous_access(page)
            await self._run_search(page, start, today)
            filings = await self._collect(page)
        except Exception as e:
            self.last_error = str(e)
            log.error("Sarasota FL: scrape failed: %s", e, exc_info=True)
        finally:
            await self._close_browser()

        unique = {f.case_number: f for f in filings}
        result = list(unique.values())
        log.info("Sarasota FL: %d eviction filings found", len(result))
        return result

    # ------------------------------------------------------------------ #
    #  Navigation / search                                                 #
    # ------------------------------------------------------------------ #

    async def _anonymous_access(self, page) -> None:
        log.info("Sarasota FL: entering ClerkNet as anonymous public user")
        await page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(2_500)
        await page.click("a:has-text('General')")
        await page.wait_for_timeout(2_500)
        await page.click("input[value='Agree'], button:has-text('Agree')")
        await page.wait_for_timeout(2_500)

    async def _run_search(self, page, start: date, today: date) -> None:
        await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(4_000)

        log.info("Sarasota FL: searching Civil %s → %s", start.isoformat(), today.isoformat())
        # Drive the Telerik combo + date pickers via their client API.
        await page.evaluate(
            """({y1, m1, d1, y2, m2, d2}) => {
                $find("ctl00_cphBody_rcbCourtType").findItemByText("Civil").select();
                $find("ctl00_cphBody_rdStart").set_selectedDate(new Date(y1, m1, d1));
                $find("ctl00_cphBody_rdEnd").set_selectedDate(new Date(y2, m2, d2));
            }""",
            {"y1": start.year, "m1": start.month - 1, "d1": start.day,
             "y2": today.year, "m2": today.month - 1, "d2": today.day},
        )
        await page.wait_for_timeout(2_000)
        await page.click("#ctl00_cphBody_btnSearch, input[value='Search']")
        await page.wait_for_timeout(5_000)

        # Bump page size to the maximum (50) via the pager combo to cut the
        # page count (the grid client API breaks the grid; the combo rebinds
        # cleanly).
        try:
            await page.click("[id*='PageSizeComboBox_Arrow']")
            await page.wait_for_timeout(1_500)
            await page.click(
                f"xpath=//div[contains(@id,'PageSizeComboBox_DropDown')]"
                f"//li[normalize-space(text())='{PAGE_SIZE}']"
            )
            await page.wait_for_timeout(5_000)
        except Exception as e:
            log.debug("Sarasota FL: could not raise page size: %s", e)

    async def _collect(self, page) -> list[Filing]:
        filings: list[Filing] = []
        pager = await page.evaluate(_PAGER_TEXT_JS)
        if pager:
            log.info("Sarasota FL: %d civil items across %d pages", pager["items"], pager["pages"])

        for page_idx in range(1, self.max_pages + 1):
            rows = await page.evaluate(_GRID_ROWS_JS)
            if not rows:
                break

            page_filings = [f for f in (self._row_to_filing(r) for r in rows) if f]
            filings.extend(page_filings)
            log.info("Sarasota FL: page %d — %d rows, %d evictions",
                     page_idx, len(rows), len(page_filings))

            if not await self._next_page(page, rows[0][0] if rows else None):
                break

        return filings

    async def _next_page(self, page, first_case_before: str | None) -> bool:
        btn = await page.query_selector("input.rgPageNext:not(.rgPagerButtonDisabled)")
        if not btn:
            return False
        try:
            await btn.click()
        except Exception:
            return False
        await page.wait_for_timeout(3_000)
        # Confirm the page actually advanced (first case number changed).
        after = await page.evaluate(_GRID_ROWS_JS)
        first_after = after[0][0] if after else None
        return bool(first_after) and first_after != first_case_before

    # ------------------------------------------------------------------ #
    #  Parsing (pure / unit-tested)                                        #
    # ------------------------------------------------------------------ #

    @classmethod
    def _row_to_filing(cls, cells: list[str]) -> Filing | None:
        # Columns: Case Number | Status | Primary Party | Secondary Party |
        #          Case File Date | Case Type [| pager]
        if len(cells) < 6 or not _CASE_NUM_RE.match(cells[0]):
            return None
        case_raw, _status, primary, secondary, file_raw, case_type = cells[:6]

        if "evict" not in case_type.lower():
            return None

        filing_date = cls._parse_date(file_raw)
        if not filing_date:
            return None

        landlord = cls._clean_party(primary) or "Unknown"
        tenant_raw = cls._clean_party(secondary)
        tenant = clean_tenant_name(tenant_raw) or tenant_raw or "Unknown"

        return Filing(
            case_number      = cls._normalize_case_number(case_raw),
            tenant_name      = tenant,
            property_address = "Unknown",
            landlord_name    = landlord,
            filing_date      = filing_date,
            court_date       = None,
            state            = STATE,
            county           = COUNTY,
            notice_type      = NOTICE_TYPE,
            source_url       = SOURCE_URL,
        )

    @staticmethod
    def _clean_party(raw: str) -> str:
        """Take the first party, dropping the "(Plaintiff)"/"(Defendant)" tags
        and any trailing alias parties."""
        if not raw:
            return ""
        first = _PARTY_TAG_RE.split(raw)[0]
        return re.sub(r"\s+", " ", first).strip()

    @staticmethod
    def _parse_date(raw: str) -> date | None:
        m = _FILE_DATE_RE.search(raw or "")
        if not m:
            return None
        try:
            return datetime.strptime(m.group(1), "%m/%d/%Y").date()
        except ValueError:
            return None

    @staticmethod
    def _normalize_case_number(raw: str) -> str:
        # "2026 CC 005586 NC" -> "2026-CC-005586-NC"
        return re.sub(r"\s+", "-", raw.strip())
