from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta

from models.filing import Filing
from scrapers.base_scraper import BaseScraper
from scrapers.dates import court_today

log = logging.getLogger(__name__)

# Miami-Dade Clerk Online Case Search (OCS)
# https://www2.miamidadeclerk.gov/ocs/
# Civil cases — category "RE" covers Residential Eviction / Removal of Tenant

PORTAL_URL = "https://www2.miamidadeclerk.gov/ocs/Search.aspx"
SOURCE_URL = "https://www2.miamidadeclerk.gov/ocs/"

STATE = "FL"
COUNTY = "Miami-Dade"
COURT_TIMEZONE = "America/New_York"
NOTICE_TYPE = "Residential Eviction"

# JavaScript injected into browser context to call the OCS AJAX search endpoint.
# The OCS portal is an ASP.NET WebForms app that uses __EVENTTARGET / __EVENTARGUMENT
# for postbacks and returns HTML fragments. We use fetch from page context to reuse
# session cookies and bypass CORS.
_JS_SEARCH = r"""
async (payload) => {
    try {
        const params = new URLSearchParams(payload);
        const r = await fetch('/ocs/Search.aspx', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'X-Requested-With': 'XMLHttpRequest',
                'Accept': 'text/html,application/xhtml+xml,*/*',
            },
            body: params.toString(),
            credentials: 'same-origin',
        });
        if (!r.ok) return {_error: `HTTP ${r.status}`};
        return {html: await r.text()};
    } catch(e) {
        return {_error: e.toString()};
    }
}
"""


class MiamiDadeScraper(BaseScraper):
    """
    Scrapes Miami-Dade County Clerk OCS (Online Case Search) for Residential
    Eviction filings. Uses Playwright to load the portal page (to get session
    tokens / ViewState), then injects JS fetch calls to POST the search form
    and parse results.

    Portal: https://www2.miamidadeclerk.gov/ocs/
    Case type searched: RE (Residential Eviction)

    If the portal structure has changed or returns errors, logs a warning
    and returns [] without raising.
    """

    def __init__(self, lookback_days: int = 2, headless: bool = True):
        super().__init__(headless=headless)
        self.lookback_days = lookback_days

    async def scrape(self) -> list[Filing]:
        today = court_today(COURT_TIMEZONE)
        start = today - timedelta(days=self.lookback_days)
        filings: list[Filing] = []

        page = await self._launch_browser()
        try:
            log.info("Miami-Dade FL: navigating to OCS portal")
            try:
                await page.goto(SOURCE_URL, wait_until="networkidle", timeout=60_000)
            except Exception as e:
                log.warning(f"Miami-Dade FL: failed to load portal: {e}")
                return []

            await page.wait_for_timeout(2000)

            # Extract ASP.NET form tokens needed for postback
            viewstate = await self._get_input_value(page, "#__VIEWSTATE")
            viewstate_gen = await self._get_input_value(page, "#__VIEWSTATEGENERATOR")
            event_validation = await self._get_input_value(page, "#__EVENTVALIDATION")

            if not viewstate:
                log.warning(
                    "Miami-Dade FL: could not find __VIEWSTATE — portal structure may "
                    "have changed or requires JS rendering. Trying direct form interaction."
                )
                filings = await self._scrape_via_form(page, start, today)
                return filings

            filings = await self._search_by_date(
                page, start, today, viewstate, viewstate_gen, event_validation
            )

        except Exception as e:
            log.error(f"Miami-Dade FL scrape failed: {e}", exc_info=True)
            return []
        finally:
            await self._close_browser()

        log.info(f"Miami-Dade FL: {len(filings)} filings found")
        return filings

    async def _scrape_via_form(self, page, start: date, today: date) -> list[Filing]:
        """Fallback: use Playwright UI automation to fill and submit the search form."""
        filings: list[Filing] = []
        try:
            # Try to find a case type dropdown and date fields
            # OCS uses a "Search by Date Filed" tab with CaseType dropdown
            await page.wait_for_selector("select, input[type='text']", timeout=10_000)

            # Look for case type dropdown
            case_type_sel = await page.query_selector("select[name*='CaseType'], select[id*='CaseType'], select[id*='caseType']")
            if case_type_sel:
                # Select Residential Eviction — try common option texts
                for label in ("Residential Eviction", "RE", "Eviction"):
                    try:
                        await case_type_sel.select_option(label=label)
                        break
                    except Exception:
                        pass

            # Try date range fields
            start_str = start.strftime("%m/%d/%Y")
            end_str = today.strftime("%m/%d/%Y")

            for sel_id in ("txtFromDate", "txtStartDate", "txtDateFrom", "FromDate"):
                el = await page.query_selector(f"#{sel_id}, input[name*='{sel_id}']")
                if el:
                    await el.fill(start_str)
                    break

            for sel_id in ("txtToDate", "txtEndDate", "txtDateTo", "ToDate"):
                el = await page.query_selector(f"#{sel_id}, input[name*='{sel_id}']")
                if el:
                    await el.fill(end_str)
                    break

            # Submit
            submit_btn = await page.query_selector("input[type='submit'], button[type='submit']")
            if submit_btn:
                await submit_btn.click()
                await page.wait_for_load_state("networkidle", timeout=30_000)
                filings = await self._parse_results_page(page, today)

        except Exception as e:
            log.warning(f"Miami-Dade FL form interaction failed: {e}")

        return filings

    async def _search_by_date(
        self,
        page,
        start: date,
        today: date,
        viewstate: str,
        viewstate_gen: str,
        event_validation: str,
    ) -> list[Filing]:
        """Post the ASP.NET search form via injected fetch."""
        start_str = start.strftime("%m/%d/%Y")
        end_str = today.strftime("%m/%d/%Y")

        payload = {
            "__VIEWSTATE": viewstate,
            "__VIEWSTATEGENERATOR": viewstate_gen,
            "__EVENTVALIDATION": event_validation,
            "__EVENTTARGET": "",
            "__EVENTARGUMENT": "",
            # Common OCS field names — adjust if portal fields differ
            "ctl00$ContentPlaceHolder1$ddlCaseType": "RE",
            "ctl00$ContentPlaceHolder1$txtFromDate": start_str,
            "ctl00$ContentPlaceHolder1$txtToDate": end_str,
            "ctl00$ContentPlaceHolder1$btnSearch": "Search",
        }

        data = await page.evaluate(_JS_SEARCH, payload)

        if not isinstance(data, dict) or "_error" in data:
            err = data.get("_error") if isinstance(data, dict) else "unexpected response"
            log.warning(f"Miami-Dade FL search API error: {err}")
            # Fall back to UI form interaction
            return await self._scrape_via_form(page, start, today)

        html = data.get("html", "")
        if not html:
            log.warning("Miami-Dade FL: empty response from search")
            return []

        return self._parse_html_results(html, today)

    async def _parse_results_page(self, page, today: date) -> list[Filing]:
        """Parse results from the current Playwright page."""
        try:
            html = await page.content()
            return self._parse_html_results(html, today)
        except Exception as e:
            log.warning(f"Miami-Dade FL: failed to parse results page: {e}")
            return []

    def _parse_html_results(self, html: str, today: date) -> list[Filing]:
        """Parse case rows from OCS HTML results table."""
        try:
            from html.parser import HTMLParser

            class TableParser(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.rows: list[list[str]] = []
                    self._in_table = False
                    self._in_row = False
                    self._current_row: list[str] = []
                    self._current_cell: list[str] = []
                    self._cell_depth = 0

                def handle_starttag(self, tag, attrs):
                    attrs_dict = dict(attrs)
                    if tag == "table":
                        self._in_table = True
                    elif tag == "tr" and self._in_table:
                        self._in_row = True
                        self._current_row = []
                    elif tag in ("td", "th") and self._in_row:
                        self._cell_depth += 1
                        if self._cell_depth == 1:
                            self._current_cell = []

                def handle_endtag(self, tag):
                    if tag in ("td", "th") and self._cell_depth > 0:
                        self._cell_depth -= 1
                        if self._cell_depth == 0 and self._in_row:
                            self._current_row.append(" ".join(self._current_cell).strip())
                    elif tag == "tr" and self._in_row:
                        if self._current_row:
                            self.rows.append(self._current_row)
                        self._in_row = False
                    elif tag == "table":
                        self._in_table = False

                def handle_data(self, data):
                    if self._cell_depth > 0:
                        stripped = data.strip()
                        if stripped:
                            self._current_cell.append(stripped)

            parser = TableParser()
            parser.feed(html)

            filings: list[Filing] = []
            for row in parser.rows:
                if len(row) < 3:
                    continue
                # Skip header rows
                if any(h in row[0].lower() for h in ("case number", "case#", "no.", "number")):
                    continue

                filing = self._row_to_filing(row, today)
                if filing:
                    filings.append(filing)

            return filings

        except Exception as e:
            log.warning(f"Miami-Dade FL: HTML parse error: {e}")
            return []

    def _row_to_filing(self, cells: list[str], today: date) -> Filing | None:
        """Convert a result table row to a Filing. Column order may vary."""
        try:
            # OCS typical columns: CaseNumber, FilingDate, CaseType, Plaintiff, Defendant, Address
            if len(cells) < 4:
                return None

            case_number = cells[0].strip()
            if not case_number or case_number.lower() in ("case number", "no."):
                return None

            # Try to parse a date from available cells
            filing_date: date = today
            for cell in cells[1:4]:
                d = self._try_parse_date(cell)
                if d:
                    filing_date = d
                    break

            # Plaintiff / landlord (4th or 5th column typically)
            landlord = cells[3].strip() if len(cells) > 3 else "Unknown"
            tenant = cells[4].strip() if len(cells) > 4 else "Unknown"
            address = cells[5].strip() if len(cells) > 5 else "Unknown"

            if not landlord:
                landlord = "Unknown"
            if not tenant:
                tenant = "Unknown"
            if not address:
                address = "Unknown"

            return Filing(
                case_number=case_number,
                tenant_name=tenant,
                property_address=address,
                landlord_name=landlord,
                filing_date=filing_date,
                court_date=None,
                state=STATE,
                county=COUNTY,
                notice_type=NOTICE_TYPE,
                source_url=SOURCE_URL,
            )
        except Exception as e:
            log.debug(f"Miami-Dade FL: skipping row {cells!r}: {e}")
            return None

    @staticmethod
    async def _get_input_value(page, selector: str) -> str:
        try:
            el = await page.query_selector(selector)
            if el:
                return (await el.get_attribute("value")) or ""
        except Exception:
            pass
        return ""

    @staticmethod
    def _try_parse_date(raw: str) -> date | None:
        raw = raw.strip()
        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
        return None
