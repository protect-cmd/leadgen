from __future__ import annotations
import logging
from datetime import date, datetime
from scrapers.base_scraper import BaseScraper
from models.filing import Filing

log = logging.getLogger(__name__)

PORTAL_URL = "https://www.lacourt.ca.gov/newfilings/ui/index.aspx"
SOURCE_URL = PORTAL_URL

# Selectors — fill in from docs/portal_notes.md after running scratch_la_discover.py
SELECTOR_CASE_TYPE_DROPDOWN = ""   # e.g. "select#caseType"
SELECTOR_CASE_TYPE_UD_OPTION = ""  # e.g. "Unlawful Detainer"
SELECTOR_DATE_INPUT = ""           # leave blank if portal defaults to today
SELECTOR_RESULT_ROWS = ""          # e.g. "table.results tbody tr"
SELECTOR_ROW_CASE_NUMBER = ""      # e.g. "td:nth-child(1) a"
SELECTOR_ROW_TENANT_NAME = ""      # e.g. "td:nth-child(2)"
SELECTOR_ROW_ADDRESS = ""          # e.g. "td:nth-child(3)"
SELECTOR_ROW_FILING_DATE = ""      # e.g. "td:nth-child(4)"
SELECTOR_NEXT_PAGE = ""            # e.g. "a.next-page"
SELECTOR_DETAIL_COURT_DATE = ""    # e.g. "#courtDate"
SELECTOR_DETAIL_LANDLORD = ""      # e.g. "#plaintiffName"

NOTICE_TYPE = "Unlawful Detainer"
STATE = "CA"
COUNTY = "Los Angeles"


class LosAngelesScraper(BaseScraper):

    async def scrape(self) -> list[Filing]:
        page = await self._launch_browser()
        filings: list[Filing] = []

        try:
            await page.goto(PORTAL_URL, wait_until="networkidle")

            if SELECTOR_CASE_TYPE_DROPDOWN:
                await page.select_option(SELECTOR_CASE_TYPE_DROPDOWN, label=SELECTOR_CASE_TYPE_UD_OPTION)
                await page.wait_for_load_state("networkidle")

            while True:
                rows = await page.query_selector_all(SELECTOR_RESULT_ROWS)
                if not rows:
                    log.info("No result rows found on current page")
                    break

                for row in rows:
                    try:
                        case_number = await self._text(row, SELECTOR_ROW_CASE_NUMBER)
                        tenant_name = await self._text(row, SELECTOR_ROW_TENANT_NAME)
                        address = await self._text(row, SELECTOR_ROW_ADDRESS)
                        filing_date_raw = await self._text(row, SELECTOR_ROW_FILING_DATE)
                        filing_date = self._parse_date(filing_date_raw)

                        detail_url = await self._href(row, SELECTOR_ROW_CASE_NUMBER)
                        court_date, landlord_name = await self._fetch_detail(page, detail_url)

                        filings.append(Filing(
                            case_number=case_number.strip(),
                            tenant_name=tenant_name.strip(),
                            property_address=address.strip(),
                            landlord_name=landlord_name.strip(),
                            filing_date=filing_date,
                            court_date=court_date,
                            state=STATE,
                            county=COUNTY,
                            notice_type=NOTICE_TYPE,
                            source_url=detail_url or SOURCE_URL,
                        ))
                    except Exception as e:
                        log.warning(f"Failed to parse row: {e}")
                        continue

                next_btn = await page.query_selector(SELECTOR_NEXT_PAGE)
                if not next_btn:
                    break
                await next_btn.click()
                await page.wait_for_load_state("networkidle")

        finally:
            await self._close_browser()

        log.info(f"LA scraper returned {len(filings)} filings")
        return filings

    async def _fetch_detail(self, page, url: str) -> tuple[date | None, str]:
        if not url:
            return None, ""
        await page.goto(url, wait_until="networkidle")
        court_date_raw = await self._text(page, SELECTOR_DETAIL_COURT_DATE, default="")
        landlord_raw = await self._text(page, SELECTOR_DETAIL_LANDLORD, default="")
        court_date = self._parse_date(court_date_raw) if court_date_raw else None
        await page.go_back(wait_until="networkidle")
        return court_date, landlord_raw

    @staticmethod
    async def _text(element, selector: str, default: str = "") -> str:
        el = await element.query_selector(selector)
        if not el:
            return default
        return (await el.inner_text()).strip()

    @staticmethod
    async def _href(element, selector: str) -> str:
        el = await element.query_selector(selector)
        if not el:
            return ""
        return await el.get_attribute("href") or ""

    @staticmethod
    def _parse_date(raw: str) -> date:
        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
            try:
                return datetime.strptime(raw.strip(), fmt).date()
            except ValueError:
                continue
        raise ValueError(f"Cannot parse date: {raw!r}")
