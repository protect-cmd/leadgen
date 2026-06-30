from __future__ import annotations

"""
Palm Beach County FL — eCaseView eviction scraper.

Portal: https://appsgp.mypalmbeachclerk.com/ecaseview/
- Guest login (no account needed)
- F5 BIG-IP WAF on login + reCAPTCHA v3 on search
- reCAPTCHA v3 + F5 BIG-IP WAF on login/search — bypassed via Bright Data
  Scraping Browser (set BRIGHTDATA_SB_WS). Falls back to local Playwright
  Chromium + playwright-stealth for dev, but reCAPTCHA blocks headless in prod.
- County Civil (CC) court type, file-date-range search
- Results capped at 200 per search — splits into ≤_WINDOW_DAYS-day windows
- Property address extracted from the Complaint for Tenant Eviction PDF
  (publicly viewable as guest — no registered account needed)

Requires: pdfplumber (`pip install pdfplumber`). No Chrome installation needed.
"""

import asyncio
import io
import logging
import os
import platform
import re
from datetime import date, datetime, timedelta
from typing import Optional

from playwright.async_api import Page

from models.filing import Filing
from scrapers.base_scraper import BaseScraper
from scrapers.dates import court_today
from services.name_utils import clean_tenant_name

log = logging.getLogger(__name__)


# ── Constants ──────────────────────────────────────────────────────────────── #

ECASEVIEW_HOME  = "https://appsgp.mypalmbeachclerk.com/ecaseview/"
# POSTing to this handler triggers the search and returns results HTML
SEARCH_HANDLER  = ECASEVIEW_HOME + "Search?handler=BeginSearch"
SOURCE_URL      = ECASEVIEW_HOME
STATE           = "FL"
COUNTY          = "Palm Beach"
COURT_TIMEZONE  = "America/New_York"
NOTICE_TYPE     = "Residential Eviction"
_RECAPTCHA_KEY  = "6LesMAssAAAAAKMaRLSl1d8DFRK5qaocke3wSoJf"
_WINDOW_DAYS    = 2        # max days per search window — ~50 CC cases/day, cap is 200
_VS_RE          = re.compile(r"\s+vs?\.?\s+", re.IGNORECASE)

# Eviction case type codes from Palm Beach eCaseView (verified via live portal 2026-06-30).
# These are the `value` attributes of the <option> tags in #SearchRequest_CaseType
# when CourtType = CC. The portal AJAX endpoint is:
#   GET /ecaseview/Search?handler=CaseTypes&courtType=CC
_EVICTION_CASE_TYPES = [
    "EV",    # EVICTION NON-MONETARY
    "EV2",   # EVICTION WITH DAMAGES $1-$2500
    "EV3",   # EVICTION WITH DAMAGES OVER $2500
    "EV4",   # EVICTION WITH DAMAGES $2501-$15000
    "EV5",   # EVICTION WITH DAMAGES $15001-$30000
    "EV6",   # EVICTION COMMERCIAL NON MONETARY
    "EV7",   # EVICTION COMMERCIAL $1-$2500
    "EV8",   # EVICTION COMMERCIAL $2501-15000
    "EV9",   # EVICTION COMMERCIAL $15001-$30000
    "EV10",  # EVICTION RESIDENTIAL $30001-$50000
    "EV11",  # EVICTION COMMERCIAL $30001-$50000
]

# Generic legal placeholders that courts list as defendants alongside real tenants.
# When a case has both a placeholder and a real named defendant, we prefer the real name.
_PLACEHOLDER_TENANTS: frozenset[str] = frozenset({
    "ALL OTHERS IN POSSESSION",
    "ALL UNKNOWN OCCUPANTS IN POSSESSION",
    "ALL UNKNOWN OCCUPANTS",
    "ALL UNKNOWN PARTIES IN POSSESSION",
    "ALL UNKNOWN PARTIES",
    "UNKNOWN OCCUPANT",
    "UNKNOWN TENANT",
    "DOES 1-10",
    "DOES 1 THROUGH 10",
})


# ── JavaScript helpers ────────────────────────────────────────────────────── #

# Set a <select> value via the native React/Razor setter (avoids SPA ignoring assignment).
_JS_SET_SELECT = r"""
({id, value}) => {
    const el = document.querySelector('#' + id);
    if (!el) return false;
    const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set;
    setter.call(el, value);
    el.dispatchEvent(new Event('change', {bubbles: true}));
    return el.value;
}
"""

# Set a date <input> value via the native setter.
_JS_SET_INPUT = r"""
({id, value}) => {
    const el = document.querySelector('#' + id);
    if (!el) return false;
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
    setter.call(el, value);
    el.dispatchEvent(new Event('input',  {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
    return el.value;
}
"""

# Fill the search form, get a real reCAPTCHA v3 token from the loaded grecaptcha
# library, then POST via fetch (no page navigation). Returns the results page HTML.
# Must run inside a page that already has grecaptcha loaded (i.e. after guest login).
# caseType: case type code to filter on (e.g. 'EV2' = EVICTION WITH DAMAGES $1-$2500).
#           Pass '' or omit to search ALL case types.
_JS_SEARCH_VIA_FETCH = r"""
async ({startDate, endDate, caseType, target, siteKey}) => {
    // Get a real reCAPTCHA v3 token from the page's own grecaptcha instance
    const token = await new Promise((resolve, reject) => {
        if (typeof grecaptcha === 'undefined') {
            reject(new Error('grecaptcha not loaded on this page'));
            return;
        }
        const t = setTimeout(() => reject(new Error('grecaptcha.execute timed out after 30s')), 30000);
        grecaptcha.ready(() => {
            clearTimeout(t);
            grecaptcha.execute(siteKey, {action: 'case_search'}).then(resolve).catch(reject);
        });
    });

    // Collect base form data — picks up __RequestVerificationToken automatically
    const form = document.querySelector('form');
    if (!form) throw new Error('search form not found on page');
    const fd = new FormData(form);

    // Override fields directly — no DOM events, no race conditions with AJAX
    fd.set('RecaptchaToken', token);
    fd.set('SearchRequest.CourtType', 'CC');
    fd.set('SearchRequest.CourtTypeText', 'County Civil');
    fd.set('SearchRequest.FileBeginDate', startDate);
    fd.set('SearchRequest.FileEndDate', endDate);
    if (caseType) {
        fd.set('SearchRequest.CaseType', caseType);
    }

    // POST via fetch — browser follows the POST→redirect→GET automatically,
    // returning the final Search Results HTML without page navigation.
    const resp = await fetch(target, {method: 'POST', body: fd});
    if (!resp.ok) throw new Error('search POST returned ' + resp.status);
    return await resp.text();
}
"""

# Parse the raw results HTML and return a list of row objects.
# Table[0] = search-criteria summary; Table[1] = the actual results grid.
# Note: the 'dataTable' class is added by JS on page load — not present in raw HTML.
_JS_PARSE_RESULTS_HTML = r"""
(html) => {
    const parser = new DOMParser();
    const doc    = parser.parseFromString(html, 'text/html');
    const norm   = s => (s || '').replace(/[\\s]+/g, ' ').trim();

    // Exclude the search-criteria summary (#searchCriteria) — it always exists.
    // The first remaining table is the results grid (only present when cases found).
    const tables = [...doc.querySelectorAll('table')].filter(t => t.id !== 'searchCriteria');
    const table  = tables[0];
    if (!table) return [];

    const heads  = [...table.querySelectorAll('thead th')]
        .map(th => norm(th.innerText).toLowerCase());
    const idxOf  = frag => heads.findIndex(h => h.includes(frag));

    const iStyle  = idxOf('case style');
    const iFiled  = idxOf('file date');
    const iType   = idxOf('case type');
    const iStatus = idxOf('status');

    const rows = [...table.querySelectorAll('tbody tr')];
    return rows.map(r => {
        const cells = [...r.querySelectorAll('td')];
        const cell  = i => (i >= 0 && i < cells.length) ? norm(cells[i].innerText) : '';
        const btn   = r.querySelector('button');
        return {
            case_number: btn ? norm(btn.innerText) : cell(0),
            case_style:  cell(iStyle),
            filed:       cell(iFiled),
            case_type:   cell(iType),
            status:      cell(iStatus),
        };
    }).filter(r => r.case_number && !/case.?number/i.test(r.case_number));
}
"""

# Locate the complaint document row on the Dockets tab and return its formaction URL.
# formaction is a GET URL: /ecaseview/CaseData/Dockets?handler=ViewImage&DocketId=…&Din=…
_JS_GET_COMPLAINT_FORMACTION = r"""
() => {
    const norm = s => (s || '').replace(/[\\s]+/g, ' ').trim().toUpperCase();
    const rows = [...document.querySelectorAll('table tr')];

    // Try most-specific match first, fall back to any complaint row
    const patterns = [
        r => norm(r.innerText).includes('COMPLAINT FOR TENANT EVICTION'),
        r => norm(r.innerText).includes('COMPLAINT FOR REMOVAL'),
        r => norm(r.innerText).includes('COMPLAINT') && !norm(r.innerText).includes('ANSWER TO COMPLAINT'),
    ];

    let targetRow = null;
    for (const test of patterns) {
        targetRow = rows.find(test);
        if (targetRow) break;
    }
    if (!targetRow) return null;

    // Find the view button via formaction (class name varies by portal version)
    // Fall back to green-icon img, then .iconimage class
    const btn = targetRow.querySelector('button[formaction]')
             || targetRow.querySelector('button img[src*="page_green"]')?.closest('button')
             || targetRow.querySelector('button.iconimage');
    return btn ? btn.getAttribute('formaction') : null;
}
"""


# Mark the complaint button with a data attribute so Playwright can click it
# with a trusted (isTrusted=true) mouse event — required for reCAPTCHA to fire.
_JS_MARK_COMPLAINT_BUTTON = r"""
() => {
    const norm = s => (s || '').replace(/[\\s]+/g, ' ').trim().toUpperCase();
    const rows = [...document.querySelectorAll('table tr')];
    const patterns = [
        r => norm(r.innerText).includes('COMPLAINT FOR TENANT EVICTION'),
        r => norm(r.innerText).includes('COMPLAINT FOR REMOVAL'),
        r => norm(r.innerText).includes('COMPLAINT') && !norm(r.innerText).includes('ANSWER TO COMPLAINT'),
    ];
    let targetRow = null;
    for (const test of patterns) {
        targetRow = rows.find(test);
        if (targetRow) break;
    }
    if (!targetRow) return false;
    const btn = targetRow.querySelector('button[formaction]')
             || targetRow.querySelector('button img[src*="page_green"]')?.closest('button')
             || targetRow.querySelector('button.iconimage');
    if (!btn) return false;
    btn.setAttribute('data-pb-complaint', 'true');
    return true;
}
"""


# ── Bright Data helper ────────────────────────────────────────────────────── #

def _bright_data_ws_url() -> str | None:
    """Return Bright Data Scraping Browser WebSocket URL, or None if not configured.

    Set BRIGHTDATA_SB_WS directly, or set BRIGHTDATA_CUSTOMER_ID +
    BRIGHTDATA_ZONE + BRIGHTDATA_ZONE_PASSWORD to have it built automatically.
    """
    explicit = os.getenv("BRIGHTDATA_SB_WS")
    if explicit:
        return explicit
    customer = os.getenv("BRIGHTDATA_CUSTOMER_ID")
    zone     = os.getenv("BRIGHTDATA_ZONE")
    password = os.getenv("BRIGHTDATA_ZONE_PASSWORD")
    if customer and zone and password:
        return f"wss://brd-customer-{customer}-zone-{zone}:{password}@brd.superproxy.io:9222"
    return None


# ── Scraper ───────────────────────────────────────────────────────────────── #

class PalmBeachScraper(BaseScraper):
    """
    Scrapes Palm Beach County Clerk eCaseView for eviction filings.

    Uses Bright Data Scraping Browser (BRIGHTDATA_SB_WS) to bypass the F5
    BIG-IP WAF and reCAPTCHA v3 that protect the guest-login and search steps.
    Falls back to local Playwright Chromium + playwright-stealth for development.
    No Chrome installation required — works on any device.

    The search itself is done via a JavaScript fetch() call inside the browser
    page, so no page navigation occurs between searches (faster, no re-login).
    """

    def __init__(
        self,
        lookback_days: int = 7,
        headless: bool = True,
        *,
        max_cases: int = 200,
    ):
        super().__init__(headless=headless)
        self.lookback_days = lookback_days
        self.max_cases     = max_cases
        self.last_error: Optional[str] = None

    # ------------------------------------------------------------------ #
    #  Browser launch / close                                              #
    # ------------------------------------------------------------------ #

    async def _launch_browser(self) -> Page:
        """
        Launch browser for eCaseView.

        Preferred path: Bright Data Scraping Browser (set BRIGHTDATA_SB_WS or
        BRIGHTDATA_CUSTOMER_ID + BRIGHTDATA_ZONE + BRIGHTDATA_ZONE_PASSWORD).
        Bright Data handles reCAPTCHA v3 / F5 WAF transparently.

        Fallback: local Playwright Chromium + playwright-stealth. Works for
        development but is blocked by reCAPTCHA v3 in headless production
        environments.
        """
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()

        ws_url = _bright_data_ws_url()
        if ws_url:
            log.info("Palm Beach FL: connecting to Bright Data Scraping Browser")
            self._browser = await self._playwright.chromium.connect_over_cdp(ws_url)
            context = (
                self._browser.contexts[0]
                if self._browser.contexts
                else await self._browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                    extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
                    viewport={"width": 1280, "height": 800},
                )
            )
            page = await context.new_page()
            page.set_default_timeout(120_000)
            page.set_default_navigation_timeout(60_000)
            return page

        # --- Local Playwright Chromium fallback (dev only) ---
        log.info(
            "Palm Beach FL: BRIGHTDATA_SB_WS not set — "
            "using local Playwright Chromium (may be blocked by reCAPTCHA v3 in production)"
        )
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--window-size=1280,800",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-extensions",
            ],
        )
        context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()
        page.set_default_timeout(120_000)
        page.set_default_navigation_timeout(60_000)

        from playwright_stealth import Stealth  # noqa: PLC0415
        await Stealth().apply_stealth_async(page)

        return page

    async def _close_browser(self) -> None:
        await super()._close_browser()

    # ------------------------------------------------------------------ #
    #  Entry point                                                         #
    # ------------------------------------------------------------------ #

    async def scrape(
        self,
        enrich_limit: Optional[int] = None,
        names_only: bool = False,
    ) -> list[Filing]:
        """
        enrich_limit: if set, only the first N filings are enriched (Party Names +
        address PDF). Useful for fast test runs. Production passes None (all cases).
        names_only: if True, skip the Dockets/PDF step — only extract landlord/tenant
        names from the Party Names tab. Faster for verifying name extraction.
        """
        self.last_error = None
        today = court_today(COURT_TIMEZONE)
        start = today - timedelta(days=self.lookback_days)

        # Build ≤7-day windows, oldest first
        windows: list[tuple[date, date]] = []
        win_end = today
        while win_end > start:
            win_start = max(start, win_end - timedelta(days=_WINDOW_DAYS - 1))
            windows.append((win_start, win_end))
            win_end = win_start - timedelta(days=1)
        windows.reverse()

        filings: list[Filing] = []
        enriched_so_far = 0
        try:
            page = await self._launch_browser()

            if not await self._guest_login(page):
                return []

            for i, (win_start, win_end) in enumerate(windows):
                log.info(
                    "Palm Beach FL: searching %s → %s (window %d/%d)",
                    win_start, win_end, i + 1, len(windows),
                )
                # Navigate to a fresh search form for each window after the first
                if i > 0:
                    await page.goto(
                        ECASEVIEW_HOME + "Search?handler=NewSearch",
                        wait_until="domcontentloaded",
                        timeout=60_000,
                    )
                    await self._wait_for_options(page)

                remaining: int | None = (
                    enrich_limit - enriched_so_far if enrich_limit is not None else None
                )
                window_filings = await self._run_search(
                    page, win_start, win_end,
                    names_only=names_only,
                    enrich_limit=remaining,
                )
                enriched_so_far += len(window_filings)
                filings.extend(window_filings)

                if enrich_limit is not None and enriched_so_far >= enrich_limit:
                    log.info("Palm Beach FL: enrich_limit %d reached — stopping early", enrich_limit)
                    break

        except Exception as e:
            self.last_error = str(e)
            log.error("Palm Beach FL: scrape failed: %s", e, exc_info=True)
        finally:
            await self._close_browser()

        # Deduplicate by case number
        seen: set[str] = set()
        unique: list[Filing] = []
        for f in filings:
            if f.case_number not in seen:
                seen.add(f.case_number)
                unique.append(f)

        log.info("Palm Beach FL: %d filings found", len(unique))
        return unique

    # ------------------------------------------------------------------ #
    #  Login                                                               #
    # ------------------------------------------------------------------ #

    async def _guest_login(self, page: Page) -> bool:
        """Navigate to eCaseView and click 'Login as Guest User'."""
        log.info("Palm Beach FL: navigating to eCaseView")
        await page.goto(ECASEVIEW_HOME, wait_until="domcontentloaded", timeout=60_000)

        try:
            await page.wait_for_selector(
                "button:has-text('Login as Guest')",
                state="visible",
                timeout=30_000,
            )
        except Exception as e:
            self.last_error = f"guest login button never appeared: {e}"
            log.warning("Palm Beach FL: %s", self.last_error)
            return False

        await page.click("button:has-text('Login as Guest')")

        try:
            await page.wait_for_selector(
                "#SearchRequest_CourtType",
                state="visible",
                timeout=30_000,
            )
        except Exception as e:
            self.last_error = f"search form never appeared after guest login: {e}"
            log.warning("Palm Beach FL: %s", self.last_error)
            return False

        await self._wait_for_options(page)
        log.info("Palm Beach FL: guest login successful, search form ready")
        return True

    async def _wait_for_options(self, page: Page, timeout_ms: int = 8_000) -> None:
        """Poll until the CourtType <select> has its options loaded."""
        elapsed = 0
        while elapsed < timeout_ms:
            count = await page.evaluate(
                "() => document.querySelector('#SearchRequest_CourtType')?.options.length ?? 0"
            )
            if count > 1:
                return
            await page.wait_for_timeout(300)
            elapsed += 300
        log.warning(
            "Palm Beach FL: CourtType options did not load within %dms", timeout_ms
        )

    # ------------------------------------------------------------------ #
    #  Search                                                              #
    # ------------------------------------------------------------------ #

    async def _load_eviction_case_types(self, page: Page) -> list[str]:
        """
        Return the list of eviction case type codes to search.

        Calls the portal's own CaseTypes AJAX endpoint directly — no DOM event
        triggering, no race conditions, works reliably in CDP/Playwright context.

        Falls back to the hardcoded _EVICTION_CASE_TYPES list (verified 2026-06-30)
        if the API call fails for any reason.
        """
        try:
            result: list[str] = await page.evaluate("""
                async () => {
                    const resp = await fetch(
                        '/ecaseview/Search?handler=CaseTypes&courtType=CC',
                        {credentials: 'include'}
                    );
                    const html = await resp.text();
                    const tmp = document.createElement('div');
                    tmp.innerHTML = html;
                    return [...tmp.querySelectorAll('option')]
                        .filter(o => o.text.toUpperCase().includes('EVICT'))
                        .map(o => o.value)
                        .filter(Boolean);
                }
            """)
            if result:
                log.info(
                    "Palm Beach FL: %d eviction case types from API: %r",
                    len(result), result,
                )
                return result
        except Exception as e:
            log.warning("Palm Beach FL: CaseTypes API call failed: %s", e)

        log.info("Palm Beach FL: using hardcoded eviction case types: %r", _EVICTION_CASE_TYPES)
        return list(_EVICTION_CASE_TYPES)

    async def _run_search(
        self,
        page: Page,
        start: date,
        end: date,
        *,
        names_only: bool = False,
        enrich_limit: int | None = None,
    ) -> list[Filing]:
        """
        Search for eviction cases one type at a time, enriching each batch
        immediately after its search. This is critical: the portal server stores
        only ONE search result per session, so we must enrich while that type's
        results are still in the session before the next search overwrites them.

        After each batch's enrichment the page is navigated back to the Search
        form so the next case type's _JS_SEARCH_VIA_FETCH can find the form and
        pick up a fresh __RequestVerificationToken + reCAPTCHA.
        """
        start_str = start.strftime("%Y-%m-%d")
        end_str   = end.strftime("%Y-%m-%d")

        eviction_types = await self._load_eviction_case_types(page)

        try:
            await page.wait_for_function("typeof grecaptcha !== 'undefined'", timeout=15_000)
        except Exception:
            log.warning("Palm Beach FL: grecaptcha did not load within 15s — continuing anyway")

        all_filings: list[Filing] = []
        seen_cases:  set[str]    = set()

        for case_type in eviction_types:
            if enrich_limit is not None and len(all_filings) >= enrich_limit:
                break

            log.info(
                "Palm Beach FL: fetching %s → %s  case_type=%r",
                start_str, end_str, case_type or "ALL",
            )
            try:
                html: str = await page.evaluate(
                    _JS_SEARCH_VIA_FETCH,
                    {
                        "startDate": start_str,
                        "endDate":   end_str,
                        "caseType":  case_type,
                        "target":    SEARCH_HANDLER,
                        "siteKey":   _RECAPTCHA_KEY,
                    },
                )
            except Exception as e:
                log.warning(
                    "Palm Beach FL: search fetch failed (%s→%s, type=%r): %s",
                    start_str, end_str, case_type, e,
                )
                continue

            if not html or "Search Results" not in html:
                log.warning(
                    "Palm Beach FL: response for %s→%s type=%r does not look like a "
                    "results page (reCAPTCHA score may be too low)",
                    start_str, end_str, case_type,
                )
                continue

            try:
                grid_rows: list[dict] = await page.evaluate(_JS_PARSE_RESULTS_HTML, html)
            except Exception as e:
                log.warning("Palm Beach FL: results parse failed (type=%r): %s", case_type, e)
                continue

            if log.isEnabledFor(logging.DEBUG):
                log.debug(
                    "Palm Beach FL: type=%r raw grid_rows[:3]=%r",
                    case_type, grid_rows[:3],
                )

            log.info(
                "Palm Beach FL: type=%r → %d rows", case_type or "ALL", len(grid_rows)
            )
            if len(grid_rows) >= 200:
                log.warning(
                    "Palm Beach FL: type=%r hit 200-row cap — narrow the date window",
                    case_type,
                )

            # Collect new (unseen) filings from this batch
            batch_filings: list[Filing] = [
                f for f in self._collect(grid_rows, end)
                if f.case_number not in seen_cases
            ]
            for f in batch_filings:
                seen_cases.add(f.case_number)

            if not batch_filings:
                # No new cases — session still on Search page, move to next type
                continue

            # Enrich immediately — session holds THIS type's results in SearchResults
            if enrich_limit is not None:
                remaining = enrich_limit - len(all_filings)
                to_enrich = batch_filings[:remaining]
            else:
                to_enrich = batch_filings

            await self._enrich_addresses(page, to_enrich, names_only=names_only)
            all_filings.extend(to_enrich)

            # After enrichment the page is at SearchResults. Navigate back to the
            # Search form so the next case type's fetch can find the form + CSRF token.
            if enrich_limit is None or len(all_filings) < enrich_limit:
                try:
                    await page.goto(
                        ECASEVIEW_HOME + "Search",
                        wait_until="domcontentloaded",
                        timeout=30_000,
                    )
                    await page.wait_for_function(
                        "typeof grecaptcha !== 'undefined'", timeout=15_000
                    )
                except Exception as e:
                    log.warning(
                        "Palm Beach FL: could not return to Search page after enrichment: %s", e
                    )
                    break

        log.info(
            "Palm Beach FL: %d unique eviction filings across %d case type(s)",
            len(all_filings), len(eviction_types),
        )
        return all_filings

    # ------------------------------------------------------------------ #
    #  Results parsing                                                     #
    # ------------------------------------------------------------------ #

    def _collect(self, grid_rows: list[dict], today: date) -> list[Filing]:
        filings: list[Filing] = []
        for row in grid_rows:
            if not self._is_eviction_row(row):
                continue
            f = self._grid_row_to_filing(row, today)
            if f:
                filings.append(f)
        log.info("Palm Beach FL: %d eviction filings parsed", len(filings))
        return filings

    @staticmethod
    def _is_eviction_row(row: dict) -> bool:
        case_type = (row.get("case_type") or "").upper()
        # Eviction case types from the portal: "EVICTION RESIDENTIAL ...",
        # "EVICTION COMMERCIAL ...", "EVICTION RESIDENTIAL NON-MONETARY", etc.
        if "EVICT" in case_type:
            return True
        # Other landlord/tenant keywords that may appear
        if any(k in case_type for k in ("TENANT", "LANDLORD", "REMOVAL")):
            return True
        # Blank case type — guest sees limited metadata; include conservatively
        if not case_type:
            return True
        # Everything else (COUNTY CIVIL debt, FORECLOSURE, REPLEVIN, etc.) → skip
        return False

    def _grid_row_to_filing(self, row: dict, today: date) -> Filing | None:
        case_number = (row.get("case_number") or "").strip()
        if not case_number:
            return None

        plaintiff, defendant = self._split_style(row.get("case_style") or "")
        filing_date = self._try_parse_date(row.get("filed") or "") or today

        tenant = clean_tenant_name(defendant) if defendant else ""
        notice = (row.get("case_type") or "").strip() or NOTICE_TYPE

        if "COMMERCIAL" in notice.upper():
            notice_out = notice
        elif "RESIDENTIAL" in notice.upper():
            notice_out = "Residential Eviction"
        else:
            notice_out = notice or NOTICE_TYPE

        return Filing(
            case_number=case_number,
            tenant_name=tenant or "Unknown",
            property_address="Unknown",    # enriched by _enrich_addresses() after search
            landlord_name=plaintiff or "Unknown",
            filing_date=filing_date,
            court_date=None,
            state=STATE,
            county=COUNTY,
            notice_type=notice_out,
            source_url=SOURCE_URL,
        )

    # ------------------------------------------------------------------ #
    #  Address enrichment                                                  #
    # ------------------------------------------------------------------ #

    async def _show_all_dt_rows(self, page: Page) -> None:
        """
        Set the DataTables page-length selector to 'All' (value -1) so that
        every row is visible in the DOM.  After navigation back to SearchResults,
        DataTables resets to its default (often 10 rows), so call this each time.
        """
        try:
            await page.evaluate("""
                () => {
                    const sel = document.querySelector('select[name*="_length"]');
                    if (!sel) return;
                    const nativeSel = Object.getOwnPropertyDescriptor(
                        window.HTMLSelectElement.prototype, 'value').set;
                    // Prefer value=-1 (All); fall back to the largest numeric option
                    const allOpt = [...sel.options].find(o => o.value === '-1' || o.text.trim().toUpperCase() === 'ALL');
                    const bigOpt = [...sel.options].reduce((a, b) => +a.value > +b.value ? a : b);
                    const target = allOpt || bigOpt;
                    if (target && sel.value !== target.value) {
                        nativeSel.call(sel, target.value);
                        sel.dispatchEvent(new Event('change', {bubbles: true}));
                    }
                }
            """)
            await page.wait_for_timeout(800)  # let DataTables re-render
        except Exception as e:
            log.debug("Palm Beach FL: DataTables show-all failed (non-fatal): %s", e)

    async def _enrich_addresses(
        self, page: Page, filings: list[Filing], *, names_only: bool = False
    ) -> None:
        """
        After a window search, navigate to the SearchResults page (the session
        still holds the search results) and extract the property address from
        each case's complaint PDF.  No extra reCAPTCHA needed — one per window.
        names_only: skip the Dockets/PDF step, only pull Party Names.
        """
        if not filings:
            return

        log.info("Palm Beach FL: extracting addresses for %d filings", len(filings))
        success = 0

        try:
            await page.goto(
                ECASEVIEW_HOME + "SearchResults?handler=DisplayResult",
                wait_until="domcontentloaded",
                timeout=30_000,
            )
        except Exception as e:
            log.warning("Palm Beach FL: could not reach SearchResults for enrichment: %s", e)
            return

        # Wait for the results grid to appear (absent when session has no results)
        try:
            await page.wait_for_selector(
                "table:not(#searchCriteria) tbody tr",
                timeout=10_000,
            )
        except Exception:
            log.warning("Palm Beach FL: no results table on SearchResults — session may have expired or 0 cases")
            return

        # Show all rows so DataTables pagination doesn't hide buttons for cases on page 2+
        await self._show_all_dt_rows(page)

        for i, filing in enumerate(filings):
            try:
                await self._enrich_case(page, filing, names_only=names_only)
                _addr = filing.property_address
                if _addr != "Unknown":
                    success += 1
            except Exception as e:
                log.debug("Palm Beach FL: enrich error for %s: %s", filing.case_number, e)
                # Try to restore the results page so the next case can proceed
                try:
                    await page.goto(
                        ECASEVIEW_HOME + "SearchResults?handler=DisplayResult",
                        wait_until="domcontentloaded",
                        timeout=15_000,
                    )
                    await self._show_all_dt_rows(page)
                except Exception:
                    pass

            if (i + 1) % 10 == 0:
                log.info(
                    "Palm Beach FL: enriched %d/%d — %d addresses found",
                    i + 1, len(filings), success,
                )

        log.info("Palm Beach FL: address enrichment done — %d/%d", success, len(filings))

    async def _enrich_case(
        self, page: Page, filing: Filing, *, names_only: bool = False
    ) -> None:
        """
        From the SearchResults page: navigate to CaseInfo, extract landlord/tenant
        from the Party Names tab, then (unless names_only) navigate to Dockets to
        download the complaint PDF and parse the property address.
        Updates the Filing object in place.
        """
        case_number = filing.case_number

        # JS click bypasses DataTables visibility
        clicked: bool = await page.evaluate(
            """(cn) => {
                const btn = [...document.querySelectorAll('button')]
                    .find(b => b.textContent.trim() === cn);
                if (btn) { btn.click(); return true; }
                return false;
            }""",
            case_number,
        )
        if not clicked:
            log.debug("Palm Beach FL: %s not found in results table", case_number)
            filing.property_address = "Unknown"
            return

        try:
            await page.wait_for_url("**/CaseData/CaseInfo**", timeout=15_000)
        except Exception:
            filing.property_address = "Unknown"
            try:
                await page.goto(
                    ECASEVIEW_HOME + "SearchResults?handler=DisplayResult",
                    wait_until="domcontentloaded",
                    timeout=15_000,
                )
            except Exception:
                pass
            return

        # ── Party Names tab → landlord / tenant ──────────────────────────── #
        try:
            await page.click('a:has-text("Party Names")')
            # Tab loads content dynamically — don't rely on URL change, just wait
            # for the table rows to appear
            await page.wait_for_selector("table tbody tr", timeout=10_000)

            # Dump all cells per row so we can see the real table structure
            all_rows: list[list[str]] = await page.evaluate("""
                () => [...document.querySelectorAll('table tbody tr')].map(r =>
                    [...r.querySelectorAll('td')].map(td => td.textContent.trim())
                )
            """)
            log.debug("Palm Beach FL: party table rows for %s: %r", case_number, all_rows)

            for row in all_rows:
                # Find which cell contains PLAINTIFF / DEFENDANT
                ptype = ""
                name  = ""
                for i, cell in enumerate(row):
                    cu = cell.upper()
                    if "PLAINTIFF" in cu or "DEFENDANT" in cu:
                        ptype = cu
                        # Name is in the OTHER cells joined together.
                        # Skip empty cells to avoid double spaces (e.g. empty middle-name).
                        name = " ".join(
                            row[j] for j in range(len(row)) if j != i and row[j].strip()
                        ).strip()
                        break
                if not ptype or not name:
                    continue
                if "PLAINTIFF" in ptype and (not filing.landlord_name or filing.landlord_name == "Unknown"):
                    filing.landlord_name = name.title()
                elif "DEFENDANT" in ptype:
                    # Prefer a real named tenant over a generic legal placeholder
                    # (e.g. "ALL OTHERS IN POSSESSION"). If we already have a real
                    # name keep it; if we only have a placeholder (or nothing), upgrade
                    # to a real name when one appears later in the table.
                    is_placeholder = name.upper() in _PLACEHOLDER_TENANTS
                    current_unknown = not filing.tenant_name or filing.tenant_name == "Unknown"
                    current_placeholder = (
                        not current_unknown
                        and filing.tenant_name.upper() in _PLACEHOLDER_TENANTS
                    )
                    if current_unknown or (current_placeholder and not is_placeholder):
                        filing.tenant_name = name.title()
        except Exception as e:
            log.debug("Palm Beach FL: Party Names tab error for %s: %s", case_number, e)

        if names_only:
            await page.goto(
                ECASEVIEW_HOME + "SearchResults?handler=DisplayResult",
                wait_until="domcontentloaded",
                timeout=15_000,
            )
            await self._show_all_dt_rows(page)
            return

        # ── Navigate to Dockets & Documents ───────────────────────────────── #
        try:
            await page.click('a:has-text("Dockets")')
            await page.wait_for_url("**/CaseData/Dockets**", timeout=10_000)
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_selector("table tr", timeout=5_000)
        except Exception as e:
            log.info("Palm Beach FL: Dockets tab error for %s: %s", case_number, e)
            filing.property_address = "Unknown"
            await page.goto(
                ECASEVIEW_HOME + "SearchResults?handler=DisplayResult",
                wait_until="domcontentloaded",
                timeout=15_000,
            )
            return

        # Mark the complaint button with a data attribute for a trusted click
        marked: bool = await page.evaluate(_JS_MARK_COMPLAINT_BUTTON)

        if not marked:
            docket_preview: list[str] = await page.evaluate("""
                () => [...document.querySelectorAll('table tr')]
                    .slice(1, 6)
                    .map(r => r.innerText.replace(/[\\s]+/g, ' ').trim())
                    .filter(Boolean)
            """)
            log.info(
                "Palm Beach FL: no complaint found for %s; docket rows: %r",
                case_number, docket_preview,
            )
            filing.property_address = "Unknown"
            await page.goto(
                ECASEVIEW_HOME + "SearchResults?handler=DisplayResult",
                wait_until="domcontentloaded",
                timeout=15_000,
            )
            await self._show_all_dt_rows(page)
            return

        log.debug("Palm Beach FL: clicking complaint button for %s", case_number)

        pdf_bytes: Optional[bytes] = None
        dl_fail: str = ""
        try:
            try:
                await page.click('#clearPreferenceBtn', timeout=2_000)
                log.debug("Palm Beach FL: cleared file preference for %s", case_number)
            except Exception:
                pass

            async with page.expect_download(timeout=60_000) as dl_info:
                await page.click('[data-pb-complaint="true"]')
                try:
                    # Wait for the "Download File" button to become visible — the
                    # modal text starts hidden in the DOM so waiting on text alone
                    # fails. Waiting on the button (state=visible) is reliable.
                    # 25s timeout: the portal can be slow; some cases direct-download
                    # (no modal), in which case expect_download fires before this.
                    await page.wait_for_selector(
                        'button:has-text("Download File")',
                        state="visible",
                        timeout=25_000,
                    )
                    await page.click('button:has-text("Download File")')
                    log.debug("Palm Beach FL: clicked Download File in modal for %s", case_number)
                except Exception as _modal_e:
                    log.debug("Palm Beach FL: Download File button not found for %s (%s)", case_number, _modal_e)

            download = await dl_info.value
            log.debug(
                "Palm Beach FL: download captured for %s — %s",
                case_number, download.suggested_filename,
            )
            pdf_path = await download.path()
            if pdf_path:
                with open(pdf_path, "rb") as _fh:
                    pdf_bytes = _fh.read()
                await download.delete()
                log.debug("Palm Beach FL: got %d bytes for %s", len(pdf_bytes), case_number)
            else:
                log.debug("Palm Beach FL: download.path() returned None for %s", case_number)
                dl_fail = "Unknown"

        except Exception as _dl_e:
            log.info("Palm Beach FL: download failed for %s: %s", case_number, _dl_e)
            dl_fail = "Unknown"

        # Navigate back to SearchResults regardless of download outcome
        await page.goto(
            ECASEVIEW_HOME + "SearchResults?handler=DisplayResult",
            wait_until="domcontentloaded",
            timeout=15_000,
        )
        await self._show_all_dt_rows(page)

        if not pdf_bytes:
            filing.property_address = dl_fail or "Unknown"
            return

        if len(pdf_bytes) < 500:
            log.info(
                "Palm Beach FL: download too small (%d bytes) for %s — skipping",
                len(pdf_bytes), case_number,
            )
            filing.property_address = "Unknown"
            return

        if not pdf_bytes.startswith(b'%PDF'):
            snippet = pdf_bytes[:200].decode("utf-8", errors="replace").replace("\n", " ").strip()
            log.info(
                "Palm Beach FL: not a PDF for %s (size=%d) — snippet: %s",
                case_number, len(pdf_bytes), snippet,
            )
            filing.property_address = "Unknown"
            return

        address = self._parse_complaint_address(pdf_bytes)
        filing.property_address = address

    @staticmethod
    def _ocr_pdf_pages(pdf_bytes: bytes) -> str:
        """
        OCR the first 3 pages of an image-based PDF using PyMuPDF + pytesseract.
        Returns the extracted text, or empty string if dependencies are missing.

        One-time setup:
          pip install pytesseract pillow
          Windows: install Tesseract from https://github.com/UB-Mannheim/tesseract/wiki
          macOS:   brew install tesseract
          Linux:   sudo apt install tesseract-ocr
        """
        try:
            import fitz  # PyMuPDF — noqa: PLC0415
        except ImportError:
            log.warning("Palm Beach FL: PyMuPDF not installed — run `pip install pymupdf`")
            return ""

        try:
            import pytesseract  # noqa: PLC0415
        except ImportError:
            log.warning(
                "Palm Beach FL: pytesseract not installed — "
                "run `pip install pytesseract` and install Tesseract binary "
                "(https://github.com/UB-Mannheim/tesseract/wiki on Windows)"
            )
            return ""

        # On Windows, set the Tesseract path if not already on PATH
        if platform.system() == "Windows":
            default_tess = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
            if os.path.exists(default_tess):
                pytesseract.pytesseract.tesseract_cmd = default_tess

        try:
            from PIL import Image as _PILImage  # noqa: PLC0415
        except ImportError:
            log.warning("Palm Beach FL: Pillow not installed — run `pip install pillow`")
            return ""

        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        except Exception as e:
            log.debug("Palm Beach FL: fitz failed to open PDF for OCR: %s", e)
            return ""

        parts: list[str] = []
        try:
            for page_num in range(min(3, doc.page_count)):
                page = doc[page_num]
                # 2× zoom — ~200 DPI: enough for Tesseract to read printed forms
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                img = _PILImage.open(io.BytesIO(pix.tobytes("png")))
                # PSM 6 = assume a single uniform block of text (good for form pages)
                ocr_text = pytesseract.image_to_string(img, config="--psm 6")
                if ocr_text.strip():
                    parts.append(ocr_text)
                    log.debug(
                        "Palm Beach FL: OCR page %d — %d chars", page_num, len(ocr_text)
                    )
        except Exception as e:
            log.debug("Palm Beach FL: OCR error: %s", e)
        finally:
            doc.close()

        return "\n".join(parts)

    @staticmethod
    def _parse_complaint_address(pdf_bytes: bytes) -> str:
        """
        Extract the property/premises address from a Florida Complaint for
        Tenant Eviction PDF.  Returns "Unknown" if text can't be extracted
        or no address pattern matches.

        Strategy:
        1. pdfplumber for text-layer PDFs (fast)
        2. PyMuPDF + pytesseract OCR for image-based / scanned PDFs
        """
        # —— Step 1: try fast pdfplumber text extraction —— #
        raw_text = ""
        try:
            import pdfplumber  # noqa: PLC0415
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                raw_text = "\n".join(
                    (pg.extract_text() or "") for pg in pdf.pages[:3]
                )
        except ImportError:
            pass  # no pdfplumber — go straight to OCR
        except Exception as e:
            log.debug("Palm Beach FL: pdfplumber error: %s", e)

        # Filter out watermark-only content (single chars + known stamp strings)
        _WATERMARK_RE = re.compile(
            r"^\s*[A-Z]\s*$|NOT\s+A\s+CERTIFIED\s+COPY|^FILED:", re.IGNORECASE
        )
        meaningful = "\n".join(
            ln for ln in raw_text.split("\n")
            if ln.strip() and not _WATERMARK_RE.match(ln.strip())
        )

        if len(meaningful.strip()) < 500:
            # Image-based PDF — fall back to OCR
            log.debug(
                "Palm Beach FL: PDF text layer is image-only (%d meaningful chars), trying OCR",
                len(meaningful.strip()),
            )
            raw_text = PalmBeachScraper._ocr_pdf_pages(pdf_bytes)
            if not raw_text.strip():
                log.debug("Palm Beach FL: OCR returned no text")
                return "Unknown"
            text = "\n".join(
                ln for ln in raw_text.split("\n")
                if ln.strip() and not _WATERMARK_RE.match(ln.strip())
            )
        else:
            text = meaningful

        if not text.strip():
            return "Unknown"

        log.debug("Palm Beach FL: extracted text (first 500 chars): %r", text[:500])

        # —— Pattern 0: multi-line address block after a standard label —— #
        m = re.search(
            r"(?:described\s+as|located\s+at|situated\s+at)[^\n]*\n"
            r"(?:[^\n]+\n){0,2}?"
            r"(\d{1,6}\s+[^\n]{5,60})\n"
            r"((?:Apt|Apartment|Unit|Suite|#|Lot|Space|Sp)\s*[\w-]+\n)?"
            r"([A-Za-z][^\n]+,\s*FL[^\n]*\d{5}(?:-\d{4})?)",
            text, re.IGNORECASE,
        )
        if m:
            street = m.group(1).strip()
            unit   = m.group(2).strip() if m.group(2) else ""
            city   = m.group(3).strip()
            return f"{street} {unit}, {city}".replace("  ", " ") if unit else f"{street}, {city}"

        # —— Pattern 7: address on its own line after "County, Florida:" —— #
        m = re.search(
            r"County[^\n]*Florida[^\n]*:\s*\n"
            r"(\d[^\n]{10,80}(?:FL|Florida)[,.\s]+\d{5}(?:-\d{4})?)",
            text, re.IGNORECASE,
        )
        if m:
            return m.group(1).strip()

        # —— Pattern 1: "ADDRESS OF RENTED PREMISES" label —— #
        m = re.search(
            r"ADDRESS\s+OF\s+RENTED\s+PREMISES[:\s]+([0-9][^\n]+)",
            text, re.IGNORECASE,
        )
        if m:
            addr = m.group(1).strip()
            if re.match(r'^\d+\s*$', addr):
                next_ln = re.search(r'\n\s*(\S[^\n]+)', text[m.end():])
                if next_ln:
                    addr = addr + ' ' + next_ln.group(1).strip()
            return addr

        # —— Pattern 2: "STREET ADDRESS:" label —— #
        m = re.search(
            r"STREET\s+ADDRESS[:\s]+([0-9][^\n]+)",
            text, re.IGNORECASE,
        )
        if m:
            addr = m.group(1).strip()
            if re.match(r'^\d+\s*$', addr):
                next_ln = re.search(r'\n\s*(\S[^\n]+)', text[m.end():])
                if next_ln:
                    addr = addr + ' ' + next_ln.group(1).strip()
            city_m = re.search(
                r"CITY[:\s]+([A-Za-z ]+)[,\s]+(?:STATE[:\s]+)?(?:FL|Florida)[,\s]+(?:ZIP[:\s]+)?(\d{5})",
                text, re.IGNORECASE,
            )
            if city_m:
                addr = f"{addr}, {city_m.group(1).strip()}, FL {city_m.group(2)}"
            return addr

        # —— Pattern 3: "located at / situated at / possession of" + FL address —— #
        m = re.search(
            r"(?:possession\s+of|located|situated|described\s+as|known\s+as)\s+"
            r"(?:the\s+(?:real\s+)?property\s+)?(?:located\s+)?at\s+"
            r"([0-9][^\n]{5,100}?(?:FL|Florida)[,.\s]+\d{5}(?:-\d{4})?)",
            text, re.IGNORECASE,
        )
        if m:
            addr = m.group(1).strip().rstrip(".,")
            if not re.search(r"\bSuite\b|\bSte\b", addr, re.IGNORECASE):
                return addr

        # —— Pattern 4: "property/premises/rental address:" label —— #
        m = re.search(
            r"(?:property|premises|rental)\s+address[:\s]+([0-9][^\n]+)",
            text, re.IGNORECASE,
        )
        if m:
            addr = m.group(1).strip()
            if re.match(r'^\d+\s*$', addr):
                next_ln = re.search(r'\n\s*(\S[^\n]+)', text[m.end():])
                if next_ln:
                    addr = addr + ' ' + next_ln.group(1).strip()
            return addr

        # —— Pattern 5: handwritten self-service forms —— #
        m = re.search(
            r"unit\s+number[,\s]*(?:if\s+applicable\s*)?[\]\)](.{0,300})",
            text, re.IGNORECASE | re.DOTALL,
        )
        if m:
            block = re.sub(r"\s+", " ", m.group(1)[:300]).strip()
            addr_m = re.search(
                r"(\d{1,6}\s+[A-Za-z].{5,80}?(?:FL|Florida)[,.\s]+\d{5}(?:-\d{4})?)",
                block, re.IGNORECASE,
            )
            if addr_m:
                addr = addr_m.group(1).strip()
                addr = re.sub(r"(\d{5}(?:-\d{4})?).*$", r"\1", addr)
                return addr

        # —— Pattern 6: bare FL street address —— #
        _ST_TYPE_RE = re.compile(
            r"\b(?:St|Ave|Blvd|Dr|Rd|Way|Ln|Ct|Pl|Ter|Terr|Cir|Pkwy|Hwy"
            r"|Trail|Trl|Loop|Row|Run|Sq|Pike|Path|Xing"
            r"|Street|Avenue|Boulevard|Drive|Road|Lane|Court|Place"
            r"|Terrace|Circle|Parkway|Highway|Manor|Glen|Chase|Bend"
            r"|Pass|Cove|Pointe|Ridge|Grove|Crossing|Commons)\b",
            re.IGNORECASE,
        )
        _ATT_CTX_RE = re.compile(
            r"\b(?:Esq(?:uire)?|Bar\s+(?:No|#)|Law\s+(?:Firm|Office|Group)"
            r"|Attorney\s+at\s+Law|P\.A\.|Counsel\s+for)\b",
            re.IGNORECASE,
        )
        flat = re.sub(r"\s+", " ", text)
        for m in re.finditer(
            r"(\d{1,6}\s+[A-Za-z].{5,60}?(?:FL|Florida)[,.\s]+\d{5}(?:-\d{4})?)",
            flat, re.IGNORECASE,
        ):
            addr = m.group(1).strip()
            addr = re.sub(r"(\d{5}(?:-\d{4})?).*$", r"\1", addr)
            if re.search(r"\bSuite\b|\bSte\b", addr, re.IGNORECASE):
                log.debug("Palm Beach FL: P6 skipping Suite address: %r", addr)
                continue
            if not _ST_TYPE_RE.search(addr):
                log.debug("Palm Beach FL: P6 skipping (no street type): %r", addr)
                continue
            pre_ctx = flat[max(0, m.start() - 300) : m.start()]
            if _ATT_CTX_RE.search(pre_ctx):
                log.debug("Palm Beach FL: P6 skipping attorney-context address: %r", addr)
                continue
            return addr

        log.info(
            "Palm Beach FL: no address pattern matched — text (first 500): %r", text[:500]
        )
        return "Unknown"

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _split_style(style: str) -> tuple[str, str]:
        """Split 'PLAINTIFF V DEFENDANT' on ' V ' / ' VS '."""
        if not style:
            return "", ""
        parts     = _VS_RE.split(style, maxsplit=1)
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
        try:
            parts = raw.split("/")
            if len(parts) == 3:
                m, d, y = int(parts[0]), int(parts[1]), int(parts[2])
                return date(y, m, d)
        except (ValueError, TypeError):
            pass
        return None
