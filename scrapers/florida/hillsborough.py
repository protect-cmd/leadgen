from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, timedelta
from typing import Optional

from playwright.async_api import Page

from models.filing import Filing
from scrapers.base_scraper import BaseScraper
from scrapers.dates import court_today
from services.name_utils import clean_tenant_name

log = logging.getLogger(__name__)

PORTAL_URL      = "https://hover.hillsclerk.com/"
CASE_SEARCH_URL = "https://hover.hillsclerk.com/html/case/caseSearch.html"
# Bright Data rotates residential IPs; some sessions get a degraded / WAF-
# challenged page where the search form never renders. Retry with a fresh
# session until it loads (env-overridable).
_MAX_SEARCH_ATTEMPTS = int(os.getenv("HILLSBOROUGH_MAX_ATTEMPTS", "3"))
SOURCE_URL      = PORTAL_URL
STATE           = "FL"
COUNTY          = "Hillsborough"
COURT_TIMEZONE  = "America/New_York"
NOTICE_TYPE     = "Residential Eviction"

# Confirmed from live DOM inspection (June 2026, via Bright Data Scraping
# Browser — the portal is behind PerimeterX/HUMAN and 403s datacenter/VPN IPs):
#
#   Search page defaults to the "Case Number" tab; the date-range form only
#   renders AFTER clicking #nav-DateFiled-tab.
#   - Tab:        #nav-DateFiled-tab
#   - Category:   #caseCategory       -> value "CV" (CIVIL)
#   - CaseType:   #caseTypes          -> AJAX-populated ~1-3s after category is
#                 selected; the LANDLORD/TENANT/EVICTION option value is the big
#                 comma-joined code list below.
#   - Status:     #btnCaseStatusAll   (radio)
#   - DateAfter:  #dateFiledAfter     (datepicker — set via JS value setter)
#   - DateBefore: #dateFiledBefore
#   - Submit:     #btnSubmitDateFiledSearch
#   Results grid (searchResults.html) columns:
#     0 expand button | 1 button.details | 2 Case Number | 3 Citation |
#     4 Case Style ("PLAINTIFF VS DEFENDANT") | 5 Status | 6 Filed | 7 Case Type
#   Case detail (caseSummary.html): #tabCaseParties tab -> #partiesTable, one
#   row per party. td0 = party type, td1 = <button.userDetails>NAME</button>
#   followed by <br>-separated address lines. go_back() restores the grid.
EVICTION_CASE_TYPE_VALUE = (
    "3133, 3154, 3173, 3174, 3175, 3223, 3224, 3235, 3238, "
    "35296, 35297, 35298, 35299, 35300, "
    "35768, 35769, 35770, 35771, 35772, 35773, 35774, 35775, 35776"
)


# Bright Data Scraping Browser (CDP) — same env-var convention as
# scripts/probe_wcca_brightdata.py. Returns "" when nothing is configured, in
# which case we fall back to a local Chromium (which the portal will 403).
def bright_data_ws_url() -> str:
    explicit = os.getenv("BRIGHTDATA_SB_WS")
    if explicit:
        return explicit
    customer = os.getenv("BRIGHTDATA_CUSTOMER_ID")
    zone = os.getenv("BRIGHTDATA_ZONE")
    password = os.getenv("BRIGHTDATA_ZONE_PASSWORD")
    if customer and zone and password:
        return f"wss://brd-customer-{customer}-zone-{zone}:{password}@brd.superproxy.io:9222"
    return ""


_VS_RE = re.compile(r"\s+vs\.?\s+", re.IGNORECASE)

# JS: read the results grid into structured rows, mapping by header text so a
# column reorder doesn't silently corrupt case_number / filing_date.
_JS_EXTRACT_GRID = r"""
() => {
    const norm = s => (s || '').replace(/\s+/g, ' ').trim();
    const heads = [...document.querySelectorAll('table thead th')].map(th => norm(th.innerText).toLowerCase());
    const idxOf = (frag) => heads.findIndex(h => h.includes(frag));
    let iCase  = idxOf('case number');
    let iStyle = idxOf('case style');
    let iFiled = idxOf('filed');
    let iType  = idxOf('case type');
    // Fallbacks to the confirmed default layout if headers are missing.
    if (iCase  < 0) iCase  = 2;
    if (iStyle < 0) iStyle = 4;
    if (iFiled < 0) iFiled = 6;
    if (iType  < 0) iType  = 7;
    const rows = [...document.querySelectorAll('table tbody tr')];
    return rows.map(r => {
        const c = [...r.querySelectorAll('td')];
        const cell = i => (i >= 0 && i < c.length) ? norm(c[i].innerText) : '';
        return {
            case_number: cell(iCase),
            case_style:  cell(iStyle),
            filed:       cell(iFiled),
            case_type:   cell(iType),
        };
    });
}
"""

# JS: extract every party row from the case-detail parties table.
_JS_EXTRACT_PARTIES = r"""
() => {
    const rows = [...document.querySelectorAll('#partiesTable tbody tr')];
    return rows.map(r => {
        const c = [...r.querySelectorAll('td')];
        const type = (c[0] ? c[0].innerText : '').trim();
        const cell = c[1];
        let name = '';
        if (cell) {
            const btn = cell.querySelector('button.userDetails');
            if (btn) name = btn.innerText.trim();
        }
        let address = '';
        if (cell) {
            const parts = [];
            let found = false;
            for (const node of cell.childNodes) {
                if (node.nodeType === Node.ELEMENT_NODE && node.tagName === 'A') {
                    found = true;
                    continue;
                }
                if (found) {
                    const t = (node.textContent || '').trim();
                    if (t) parts.push(t);
                }
            }
            address = parts.join(', ').trim();
        }
        return { type, name, address };
    });
}
"""


class HillsboroughScraper(BaseScraper):
    """
    Scrapes the Hillsborough County Clerk HOVER portal for residential eviction
    filings via the date-range / court-type / case-type search.

    The portal sits behind PerimeterX/HUMAN bot protection and returns HTTP 403
    to datacenter and consumer-VPN IPs. Set the Bright Data Scraping Browser
    env vars (BRIGHTDATA_SB_WS, or BRIGHTDATA_CUSTOMER_ID + BRIGHTDATA_ZONE +
    BRIGHTDATA_ZONE_PASSWORD) so the browser runs on a residential IP with
    automatic challenge solving. Without them this scraper will be blocked.

    Names, case numbers and filing dates come from the results grid in a single
    search. Property addresses require visiting each case's Parties tab, which is
    bounded by ``max_cases`` to keep proxy cost predictable; rows beyond the cap
    (or whose detail fetch fails) are still returned with property_address set to
    "Unknown".
    """

    def __init__(
        self,
        lookback_days: int = 7,
        headless: bool = True,
        *,
        max_cases: int = 200,
        fetch_addresses: bool = True,
    ):
        super().__init__(headless=headless)
        self.lookback_days = lookback_days
        self.max_cases = max_cases
        self.fetch_addresses = fetch_addresses
        # Exposed so a runner / freshness monitor can detect a silent failure
        # (matches the convention in docs/pipeline_gold_standard.md).
        self.last_error: Optional[str] = None

    # ------------------------------------------------------------------ #
    #  Browser — route through Bright Data when configured                #
    # ------------------------------------------------------------------ #

    async def _launch_browser(self) -> Page:
        ws_url = bright_data_ws_url()
        if not ws_url:
            log.warning(
                "Hillsborough FL: no Bright Data endpoint configured "
                "(BRIGHTDATA_SB_WS); falling back to local Chromium, which the "
                "portal will almost certainly 403."
            )
            return await super()._launch_browser()

        from playwright.async_api import async_playwright

        log.info("Hillsborough FL: connecting to Bright Data Scraping Browser")
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.connect_over_cdp(
            ws_url, timeout=120_000
        )
        context = (
            self._browser.contexts[0]
            if self._browser.contexts
            else await self._browser.new_context()
        )
        page = context.pages[0] if context.pages else await context.new_page()
        page.set_default_timeout(120_000)
        page.set_default_navigation_timeout(120_000)
        return page

    # ------------------------------------------------------------------ #
    #  Entry point                                                        #
    # ------------------------------------------------------------------ #

    async def scrape(self) -> list[Filing]:
        today = court_today(COURT_TIMEZONE)
        start = today - timedelta(days=self.lookback_days)
        filings: list[Filing] = []

        # Retry with a fresh Bright Data session when the search form fails to
        # render (intermittent residential-IP/WAF behaviour). A clean run that
        # simply finds no cases (no last_error) is genuine and not retried.
        for attempt in range(1, _MAX_SEARCH_ATTEMPTS + 1):
            self.last_error = None
            try:
                # Launch inside the try so a Bright Data connect failure
                # (e.g. a suspended account) is captured in last_error rather
                # than propagating uncaught and bypassing the failure signal.
                page = await self._launch_browser()
                log.info(
                    "Hillsborough FL: searching evictions %s -> %s (attempt %d/%d)",
                    start.isoformat(), today.isoformat(), attempt, _MAX_SEARCH_ATTEMPTS,
                )
                filings = await self._run_search(page, start, today)
            except Exception as e:
                self.last_error = str(e)
                log.error("Hillsborough FL: attempt %d failed: %s", attempt, e, exc_info=True)
            finally:
                await self._close_browser()

            if filings:
                break
            if not self.last_error:
                break  # clean run, genuinely no cases — don't waste a retry
            if attempt < _MAX_SEARCH_ATTEMPTS:
                log.warning(
                    "Hillsborough FL: retrying after render/session failure (%s)",
                    self.last_error,
                )

        log.info("Hillsborough FL: %d filings found", len(filings))
        return filings

    # ------------------------------------------------------------------ #
    #  Search form                                                        #
    # ------------------------------------------------------------------ #

    async def _run_search(self, page: Page, start: date, today: date) -> list[Filing]:
        await page.goto(CASE_SEARCH_URL, wait_until="domcontentloaded", timeout=120_000)

        # The date-range form is only present after the tab is clicked. If the
        # tab never appears we were almost certainly served the 403 bot wall.
        try:
            await page.wait_for_selector("#nav-DateFiled-tab", state="visible", timeout=45_000)
        except Exception as e:
            self.last_error = (
                "date-range tab never appeared (portal likely blocked / 403): "
                f"{e}"
            )
            log.warning("Hillsborough FL: %s", self.last_error)
            return []

        await page.click("#nav-DateFiled-tab")
        # The native <select> is hidden behind a Bootstrap widget, so it never
        # reports "visible". Wait for it to be attached; select_option drives
        # the hidden select directly.
        await page.wait_for_selector("#caseCategory", state="attached", timeout=30_000)

        # CIVIL category triggers the AJAX that populates #caseTypes.
        await page.select_option("#caseCategory", value="CV")
        populated = False
        for _ in range(20):  # up to ~10s for the case-type list to load
            count = await page.eval_on_selector_all("#caseTypes option", "els => els.length")
            if count > 1:
                populated = True
                break
            await page.wait_for_timeout(500)
        if not populated:
            self.last_error = "case-type dropdown did not populate after selecting CIVIL"
            log.warning("Hillsborough FL: %s", self.last_error)
            return []

        selected = await page.evaluate(
            """(targetValue) => {
                const sel = document.querySelector('#caseTypes');
                if (!sel) return 'no_select';
                for (const o of sel.options) o.selected = false;
                let opt = Array.from(sel.options).find(o => o.value === targetValue);
                if (!opt) opt = Array.from(sel.options).find(
                    o => /LANDLORD|EVICT/i.test(o.text));
                if (!opt) return 'no_option';
                opt.selected = true;
                sel.dispatchEvent(new Event('change', {bubbles: true}));
                return opt.value;
            }""",
            EVICTION_CASE_TYPE_VALUE,
        )
        if selected in ("no_select", "no_option"):
            self.last_error = f"could not select eviction case type ({selected})"
            log.warning("Hillsborough FL: %s", self.last_error)
            return []

        # Include open and closed cases.
        try:
            await page.check("#btnCaseStatusAll")
        except Exception as e:
            log.debug("Hillsborough FL: could not set case status to All: %s", e)

        start_str = start.strftime("%m/%d/%Y")
        end_str = today.strftime("%m/%d/%Y")
        await page.evaluate(
            """({after, before}) => {
                const a = document.querySelector('#dateFiledAfter');
                const b = document.querySelector('#dateFiledBefore');
                const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value').set;
                if (a) { setter.call(a, after);  a.dispatchEvent(new Event('change', {bubbles:true})); a.dispatchEvent(new Event('blur', {bubbles:true})); }
                if (b) { setter.call(b, before); b.dispatchEvent(new Event('change', {bubbles:true})); b.dispatchEvent(new Event('blur', {bubbles:true})); }
            }""",
            {"after": start_str, "before": end_str},
        )

        await page.click("#btnSubmitDateFiledSearch")
        try:
            # Generous: a busy Bright Data session can take well over a minute to
            # render the grid even though the search itself succeeds.
            await page.wait_for_selector("button.details", timeout=90_000)
        except Exception as e:
            # No rows can simply mean an empty window — not an error.
            log.info("Hillsborough FL: no result rows for window (%s)", e)
            return []
        await page.wait_for_timeout(1_500)

        return await self._collect(page, today)

    # ------------------------------------------------------------------ #
    #  Results grid + per-case detail                                     #
    # ------------------------------------------------------------------ #

    async def _collect(self, page: Page, today: date) -> list[Filing]:
        grid_rows = await page.evaluate(_JS_EXTRACT_GRID)
        log.info("Hillsborough FL: %d rows in results grid", len(grid_rows))

        filings: list[Filing] = []
        for idx, row in enumerate(grid_rows):
            base = self._grid_row_to_filing(row, today)
            if base is None:
                continue

            if self.fetch_addresses and idx < self.max_cases:
                detail = await self._fetch_detail_filings(page, idx, base, today)
                if detail:
                    filings.extend(detail)
                    continue
            filings.append(base)

        return filings

    def _grid_row_to_filing(self, row: dict, today: date) -> Filing | None:
        case_number = (row.get("case_number") or "").strip()
        if not case_number:
            return None

        plaintiff, defendant = self._split_style(row.get("case_style") or "")
        parsed = self._try_parse_date(row.get("filed") or "")
        filing_date = parsed or today
        if parsed is None:
            log.debug(
                "Hillsborough FL: no parseable filed date for %s — defaulting to today",
                case_number,
            )

        tenant = clean_tenant_name(defendant) if defendant else ""
        notice = (row.get("case_type") or "").strip() or NOTICE_TYPE

        return Filing(
            case_number=case_number,
            tenant_name=tenant or "Unknown",
            property_address="Unknown",
            landlord_name=plaintiff or "Unknown",
            filing_date=filing_date,
            court_date=None,
            state=STATE,
            county=COUNTY,
            notice_type=notice,
            source_url=SOURCE_URL,
        )

    async def _fetch_detail_filings(
        self, page: Page, idx: int, base: Filing, today: date
    ) -> list[Filing]:
        """Open case ``idx`` and return one Filing per defendant with a real
        address. Returns [] on any failure so the caller keeps the grid filing.
        """
        try:
            buttons = await page.query_selector_all("button.details")
            if idx >= len(buttons):
                return []
            await buttons[idx].click()
            await page.wait_for_selector("#tabCaseParties", state="visible", timeout=30_000)
            await page.click("#tabCaseParties")
            await page.wait_for_selector("#partiesTable tbody tr", timeout=20_000)
            parties = await page.evaluate(_JS_EXTRACT_PARTIES)
        except Exception as e:
            log.warning("Hillsborough FL: detail fetch failed for %s: %s", base.case_number, e)
            await self._return_to_results(page)
            return []

        plaintiff = base.landlord_name
        for p in parties:
            if "PLAINTIFF" in (p.get("type") or "").upper() and p.get("name"):
                plaintiff = p["name"].strip()
                break

        filings: list[Filing] = []
        for p in parties:
            if "DEFENDANT" not in (p.get("type") or "").upper():
                continue
            name = clean_tenant_name(p.get("name") or "") or base.tenant_name
            address = (p.get("address") or "").strip() or "Unknown"
            filings.append(
                Filing(
                    case_number=base.case_number,
                    tenant_name=name or "Unknown",
                    property_address=address,
                    landlord_name=plaintiff or "Unknown",
                    filing_date=base.filing_date,
                    court_date=None,
                    state=STATE,
                    county=COUNTY,
                    notice_type=base.notice_type,
                    source_url=page.url or SOURCE_URL,
                )
            )

        await self._return_to_results(page)
        return filings

    async def _return_to_results(self, page: Page) -> None:
        try:
            await page.go_back(wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_selector("button.details", timeout=30_000)
        except Exception as e:
            log.warning("Hillsborough FL: could not return to results grid: %s", e)

    # ------------------------------------------------------------------ #
    #  Helpers                                                            #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _split_style(style: str) -> tuple[str, str]:
        """Split a 'PLAINTIFF VS DEFENDANT' case style into (plaintiff, defendant)."""
        if not style:
            return "", ""
        parts = _VS_RE.split(style, maxsplit=1)
        plaintiff = parts[0].strip().strip(".,").strip()
        defendant = parts[1].strip().strip(".,").strip() if len(parts) > 1 else ""
        return plaintiff, defendant

    @staticmethod
    def _try_parse_date(raw: str) -> date | None:
        raw = (raw or "").strip()
        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
        return None
