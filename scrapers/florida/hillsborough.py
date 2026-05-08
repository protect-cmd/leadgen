from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta

from models.filing import Filing
from scrapers.base_scraper import BaseScraper
from scrapers.dates import court_today

log = logging.getLogger(__name__)

# Hillsborough County Clerk HOVER (Hillsborough Online Virtual Evidence Repository)
# https://hover.hillsclerk.com/
# Case type: Eviction / RE (Residential Eviction) under County Civil

PORTAL_URL = "https://hover.hillsclerk.com/"
CASE_SEARCH_URL = "https://hover.hillsclerk.com/html/caseSearch/caseSearch.html"
SOURCE_URL = PORTAL_URL

STATE = "FL"
COUNTY = "Hillsborough"
COURT_TIMEZONE = "America/New_York"
NOTICE_TYPE = "Residential Eviction"

# JavaScript injected into browser context to call HOVER search API.
# HOVER is an AngularJS-based SPA — we attempt the internal API endpoint
# patterns that Tyler Technologies / custom clerks use.
_JS_API_SEARCH = r"""
async (params) => {
    try {
        const qs = new URLSearchParams(params).toString();
        const endpoints = [
            '/api/CaseSearch?' + qs,
            '/CaseSearch/Search?' + qs,
            '/hover/api/search?' + qs,
        ];
        for (const url of endpoints) {
            try {
                const r = await fetch(url, {
                    method: 'GET',
                    headers: {
                        'Accept': 'application/json',
                        'X-Requested-With': 'XMLHttpRequest',
                    },
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


class HillsboroughScraper(BaseScraper):
    """
    Scrapes Hillsborough County Clerk HOVER portal for Residential Eviction
    filings. Uses Playwright to load the portal, then attempts UI automation
    on the case search form.

    Portal: https://hover.hillsclerk.com/
    Case type searched: Eviction / County Civil eviction cases

    The HOVER portal serves a 403 to non-browser clients; Playwright with
    a realistic user-agent bypasses this. If the portal remains inaccessible
    or the selectors have changed, logs a warning and returns [].
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
            log.info("Hillsborough FL: navigating to HOVER portal")
            try:
                await page.goto(PORTAL_URL, wait_until="networkidle", timeout=60_000)
            except Exception as e:
                log.warning(f"Hillsborough FL: failed to load portal: {e}")
                return []

            await page.wait_for_timeout(3000)

            # Navigate to case search
            try:
                await page.goto(CASE_SEARCH_URL, wait_until="networkidle", timeout=60_000)
                await page.wait_for_timeout(3000)
            except Exception as e:
                log.warning(f"Hillsborough FL: failed to load case search: {e}")

            filings = await self._search_via_ui(page, start, today)

        except Exception as e:
            log.error(f"Hillsborough FL scrape failed: {e}", exc_info=True)
            return []
        finally:
            await self._close_browser()

        log.info(f"Hillsborough FL: {len(filings)} filings found")
        return filings

    async def _search_via_ui(self, page, start: date, today: date) -> list[Filing]:
        """Interact with HOVER's AngularJS case search form."""
        filings: list[Filing] = []
        start_str = start.strftime("%m/%d/%Y")
        end_str = today.strftime("%m/%d/%Y")

        try:
            # Check page title / content to confirm we loaded something useful
            title = await page.title()
            log.info(f"Hillsborough FL: page title = {title!r}")

            content = await page.content()
            if "403" in content or "forbidden" in content.lower():
                log.warning(
                    "Hillsborough FL: HOVER portal returned 403/forbidden. "
                    "Portal may block automated access. Returning []."
                )
                return []

            # Try selecting case type for eviction
            for sel in (
                "select[id*='caseType']",
                "select[ng-model*='caseType']",
                "select[ng-model*='type']",
                "select[id*='type']",
                "select",
            ):
                case_type_el = await page.query_selector(sel)
                if case_type_el:
                    for val in ("Eviction", "RE", "CC", "County Civil"):
                        try:
                            await case_type_el.select_option(label=val)
                            break
                        except Exception:
                            try:
                                await case_type_el.select_option(value=val)
                                break
                            except Exception:
                                pass
                    break

            # Fill filing date range
            for id_frag in ("fileDate", "FiledDate", "fromDate", "dateFrom", "startDate", "beginDate"):
                el = await page.query_selector(
                    f"input[id*='{id_frag}'], input[name*='{id_frag}'], input[ng-model*='{id_frag}']"
                )
                if el:
                    await el.fill(start_str)
                    break

            for id_frag in ("toDate", "endDate", "thruDate", "DateTo", "endDate"):
                el = await page.query_selector(
                    f"input[id*='{id_frag}'], input[name*='{id_frag}'], input[ng-model*='{id_frag}']"
                )
                if el:
                    await el.fill(end_str)
                    break

            # Submit
            submit_btn = await page.query_selector(
                "button[type='submit'], input[type='submit'], button:has-text('Search')"
            )
            if submit_btn:
                await submit_btn.click()
                await page.wait_for_load_state("networkidle", timeout=30_000)
                await page.wait_for_timeout(2000)
                filings = await self._parse_results(page, today)
            else:
                log.warning("Hillsborough FL: could not find search submit button")

        except Exception as e:
            log.warning(f"Hillsborough FL: UI interaction failed: {e}")

        return filings

    async def _parse_results(self, page, today: date) -> list[Filing]:
        """Parse case rows from the HOVER results."""
        filings: list[Filing] = []
        try:
            rows = await page.query_selector_all(
                "table tbody tr, div.case-row, div[class*='result'], "
                "tr[ng-repeat], li[ng-repeat]"
            )
            if not rows:
                log.info("Hillsborough FL: no result rows found")
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
                    log.debug(f"Hillsborough FL: row parse failed: {e}")
                    continue

        except Exception as e:
            log.warning(f"Hillsborough FL: results parse failed: {e}")

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
            log.debug(f"Hillsborough FL: cells_to_filing failed {cells!r}: {e}")
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
