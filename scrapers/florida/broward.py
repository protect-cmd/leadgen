from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta

from models.filing import Filing
from scrapers.base_scraper import BaseScraper
from scrapers.dates import court_today

log = logging.getLogger(__name__)

# Broward County Clerk Electronic Court Access (ECA)
# https://www.browardclerk.org/Web2/CaseSearchECA/
# Category "RM" = Removal of Tenant (Residential Eviction)
# Category "RMD" = Removal of Tenant Residential & Damages

PORTAL_URL = "https://www.browardclerk.org/Web2/CaseSearchECA/"
SOURCE_URL = PORTAL_URL

STATE = "FL"
COUNTY = "Broward"
COURT_TIMEZONE = "America/New_York"
NOTICE_TYPE = "Residential Eviction"

# JavaScript injected into browser context to call Broward ECA search API.
# The ECA portal is a JavaScript SPA that calls internal REST endpoints.
# We use page.evaluate to fire the fetch with the established session cookies.
_JS_SEARCH = r"""
async (payload) => {
    try {
        const r = await fetch('/Web2/CaseSearchECA/CaseSearchResults/', {
            method: 'GET',
            headers: {
                'Accept': 'text/html,application/xhtml+xml,*/*',
                'X-Requested-With': 'XMLHttpRequest',
            },
            credentials: 'same-origin',
        });
        if (!r.ok) return {_error: `HTTP ${r.status}`};
        return {html: await r.text()};
    } catch(e) {
        return {_error: e.toString()};
    }
}
"""

# Alternative: inject fetch to the API endpoint Broward ECA likely exposes
_JS_API_SEARCH = r"""
async (params) => {
    try {
        const qs = new URLSearchParams(params).toString();
        const urls = [
            '/Web2/CaseSearchECA/api/CaseSearch?' + qs,
            '/Web2/CaseSearchECA/CaseSearch/GetResults?' + qs,
            '/Web2/api/CaseSearch?' + qs,
        ];
        for (const url of urls) {
            try {
                const r = await fetch(url, {
                    method: 'GET',
                    headers: {Accept: 'application/json'},
                    credentials: 'same-origin',
                });
                if (r.ok) {
                    const ct = r.headers.get('content-type') || '';
                    if (ct.includes('json')) return {json: await r.json(), url};
                    return {html: await r.text(), url};
                }
            } catch(e) {}
        }
        return {_error: 'no endpoint responded'};
    } catch(e) {
        return {_error: e.toString()};
    }
}
"""


class BrowardScraper(BaseScraper):
    """
    Scrapes Broward County Clerk ECA (Electronic Court Access) for Residential
    Eviction filings. Uses Playwright to load the SPA portal, then injects JS
    fetch calls to query the search API.

    Portal: https://www.browardclerk.org/Web2/CaseSearchECA/
    Case categories: RM (Removal of Tenant Residential),
                     RMD (Removal of Tenant Residential & Damages)

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
            log.info("Broward FL: navigating to ECA portal")
            try:
                await page.goto(PORTAL_URL, wait_until="networkidle", timeout=60_000)
            except Exception as e:
                log.warning(f"Broward FL: failed to load portal: {e}")
                return []

            await page.wait_for_timeout(3000)

            # Try to interact with the search form via UI automation
            filings = await self._search_via_ui(page, start, today)

        except Exception as e:
            log.error(f"Broward FL scrape failed: {e}", exc_info=True)
            return []
        finally:
            await self._close_browser()

        log.info(f"Broward FL: {len(filings)} filings found")
        return filings

    async def _search_via_ui(self, page, start: date, today: date) -> list[Filing]:
        """Use Playwright UI automation to fill the Broward ECA search form."""
        filings: list[Filing] = []
        start_str = start.strftime("%m/%d/%Y")
        end_str = today.strftime("%m/%d/%Y")

        try:
            # The ECA portal has tabs: Party Name, Case Number, etc.
            # Look for the "Party Name" tab or filing date search
            # Try clicking a "Filing Date" or date range search option
            date_tab = await page.query_selector(
                "a[href*='date'], button:has-text('Date'), a:has-text('Date Filed')"
            )
            if date_tab:
                await date_tab.click()
                await page.wait_for_timeout(1000)

            # Try to select court type = Civil
            court_type_sel = await page.query_selector(
                "select[id*='court'], select[id*='Court'], select[name*='Court']"
            )
            if court_type_sel:
                for val in ("CV", "Civil", "CIV"):
                    try:
                        await court_type_sel.select_option(value=val)
                        break
                    except Exception:
                        try:
                            await court_type_sel.select_option(label=val)
                            break
                        except Exception:
                            pass

            # Try category dropdown — "RM" or "Removal of Tenant Residential"
            category_sel = await page.query_selector(
                "select[id*='category'], select[id*='Category'], select[id*='caseType']"
            )
            if category_sel:
                for val in ("RM", "Removal of Tenant Residential"):
                    try:
                        await category_sel.select_option(value=val)
                        break
                    except Exception:
                        try:
                            await category_sel.select_option(label=val)
                            break
                        except Exception:
                            pass

            # Fill date range
            for id_frag in ("FromDate", "fromDate", "dateFrom", "DateFrom", "startDate"):
                el = await page.query_selector(f"input[id*='{id_frag}'], input[name*='{id_frag}']")
                if el:
                    await el.fill(start_str)
                    break

            for id_frag in ("ToDate", "toDate", "dateTo", "DateTo", "endDate"):
                el = await page.query_selector(f"input[id*='{id_frag}'], input[name*='{id_frag}']")
                if el:
                    await el.fill(end_str)
                    break

            # Submit search
            submit_btn = await page.query_selector(
                "button[id*='search'], button[id*='Search'], input[type='submit'], button[type='submit']"
            )
            if submit_btn:
                await submit_btn.click()
                await page.wait_for_load_state("networkidle", timeout=30_000)
                await page.wait_for_timeout(2000)
                filings = await self._parse_results(page, today)
            else:
                log.warning("Broward FL: could not find search submit button")

        except Exception as e:
            log.warning(f"Broward FL: UI form interaction failed: {e}")

        return filings

    async def _parse_results(self, page, today: date) -> list[Filing]:
        """Parse case rows from the results page."""
        filings: list[Filing] = []
        try:
            # Results may be in a table or a JS-rendered list
            rows = await page.query_selector_all(
                "table tbody tr, div.case-row, div[class*='result-row'], tr[class*='case']"
            )
            if not rows:
                log.info("Broward FL: no result rows found — may be empty or different selector needed")
                return []

            for row in rows:
                try:
                    text = await row.inner_text()
                    cells = [c.strip() for c in text.split("\t") if c.strip()]
                    if not cells:
                        cells = [c.strip() for c in text.split("\n") if c.strip()]
                    if not cells or len(cells) < 2:
                        continue

                    filing = self._cells_to_filing(cells, today)
                    if filing:
                        filings.append(filing)
                except Exception as e:
                    log.debug(f"Broward FL: row parse failed: {e}")
                    continue

        except Exception as e:
            log.warning(f"Broward FL: results parse failed: {e}")

        return filings

    def _cells_to_filing(self, cells: list[str], today: date) -> Filing | None:
        """Convert result row cells to a Filing object."""
        try:
            case_number = cells[0].strip()
            if not case_number or case_number.lower() in ("case number", "case no.", "no."):
                return None

            filing_date: date = today
            for cell in cells[1:4]:
                d = self._try_parse_date(cell)
                if d:
                    filing_date = d
                    break

            landlord = cells[2].strip() if len(cells) > 2 else "Unknown"
            tenant = cells[3].strip() if len(cells) > 3 else "Unknown"
            address = cells[4].strip() if len(cells) > 4 else "Unknown"

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
            log.debug(f"Broward FL: cells_to_filing failed {cells!r}: {e}")
            return None

    @staticmethod
    def _try_parse_date(raw: str) -> date | None:
        raw = raw.strip()
        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
        return None
