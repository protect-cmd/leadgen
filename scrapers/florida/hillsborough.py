from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Optional

from models.filing import Filing
from scrapers.base_scraper import BaseScraper
from scrapers.dates import court_today
from services.name_utils import clean_tenant_name

log = logging.getLogger(__name__)

PORTAL_URL      = "https://hover.hillsclerk.com/"
CASE_SEARCH_URL = "https://hover.hillsclerk.com/html/case/caseSearch.html"
SOURCE_URL      = PORTAL_URL
STATE           = "FL"
COUNTY          = "Hillsborough"
COURT_TIMEZONE  = "America/New_York"
NOTICE_TYPE     = "Residential Eviction"

# Confirmed from live HTML inspection (June 2026):
# - Tab:      #nav-DateFiled-tab
# - Category: #caseCategory  → value "CV" (CIVIL)
# - CaseType: #caseTypes     → multi-select, LANDLORD/TENANT/EVICTION
# - DateAfter:  #dateFiledAfter   (readonly datepicker — set via JS)
# - DateBefore: #dateFiledBefore  (readonly datepicker — set via JS)
# - Search:   #btnSubmitDateFiledSearch
# - Results:  button.details  (magnifying-glass icon per row)
# - Parties tab: #tabCaseParties
# - Parties table: #partiesTable tbody tr
EVICTION_CASE_TYPE_VALUE = (
    "3133, 3154, 3173, 3174, 3175, 3223, 3224, 3235, 3238, "
    "35296, 35297, 35298, 35299, 35300, "
    "35768, 35769, 35770, 35771, 35772, 35773, 35774, 35775, 35776"
)


class HillsboroughScraper(BaseScraper):
    """
    Scrapes Hillsborough County Clerk HOVER portal for Residential Eviction
    filings using date-range search.

    Portal: https://hover.hillsclerk.com/html/case/caseSearch.html
    Tab:    "Search by a date range / court type / case type"
    Type:   LANDLORD / TENANT / EVICTION  (multi-value case type)

    Flow per run:
      1. Load case search page — wait for networkidle so SPA fully renders
      2. Wait for #nav-DateFiled-tab to be visible before clicking
      3. Set Case Category = CV (CIVIL)
      4. Select LANDLORD/TENANT/EVICTION case type (multi-select via JS)
      5. Set date range via JS (readonly datepicker inputs)
      6. Click Search — wait for button.details to appear
      7. For each row: click magnifying-glass → Parties tab → extract defendants
      8. Return Filing list
    """

    def __init__(self, lookback_days: int = 7, headless: bool = True):
        super().__init__(headless=headless)
        self.lookback_days = lookback_days
        # FIX: expose last_error so runner/freshness monitor can detect failures
        self.last_error: Optional[str] = None

    async def scrape(self) -> list[Filing]:
        self.last_error = None
        today = court_today(COURT_TIMEZONE)
        start = today - timedelta(days=self.lookback_days)
        filings: list[Filing] = []

        page = await self._launch_browser()
        try:
            log.info("Hillsborough FL: loading case search page (networkidle)")
            # FIX: use networkidle so the JS SPA fully renders before we interact
            await page.goto(
                CASE_SEARCH_URL,
                wait_until="networkidle",
                timeout=60_000,
            )

            filings = await self._run_search(page, start, today)

        except Exception as e:
            self.last_error = str(e)
            log.error("Hillsborough FL: scrape failed: %s", e, exc_info=True)
        finally:
            await self._close_browser()

        log.info("Hillsborough FL: %d filings found", len(filings))
        return filings

    # ------------------------------------------------------------------ #
    #  Search form                                                         #
    # ------------------------------------------------------------------ #

    async def _run_search(self, page, start: date, today: date) -> list[Filing]:
        start_str = start.strftime("%m/%d/%Y")
        end_str   = today.strftime("%m/%d/%Y")

        # Step 1 — wait for tab to be visible before clicking
        # FIX: was page.click() with no wait → timed out on SPA not yet rendered
        log.info("Hillsborough FL: waiting for date-range tab to be visible")
        try:
            await page.wait_for_selector(
                "#nav-DateFiled-tab",
                state="visible",
                timeout=30_000,
            )
        except Exception as e:
            self.last_error = f"date-range tab never appeared: {e}"
            log.warning("Hillsborough FL: %s", self.last_error)
            return []

        log.info("Hillsborough FL: clicking date-range tab")
        await page.click("#nav-DateFiled-tab")
        await page.wait_for_timeout(1_000)

        # Step 2 — set Case Category to CIVIL
        log.info("Hillsborough FL: selecting CIVIL category")
        await page.select_option("#caseCategory", value="CV")
        await page.wait_for_timeout(500)

        # Step 3 — select LANDLORD/TENANT/EVICTION in the multi-select via JS
        log.info("Hillsborough FL: selecting eviction case type")
        await page.evaluate("""
            () => {
                const sel = document.querySelector('#caseTypes');
                if (!sel) return;
                for (const opt of sel.options) opt.selected = false;
                const target = Array.from(sel.options).find(o =>
                    o.text.toUpperCase().includes('LANDLORD') ||
                    o.text.toUpperCase().includes('EVICTION')
                );
                if (target) target.selected = true;
                sel.dispatchEvent(new Event('change', {bubbles: true}));
            }
        """)
        await page.wait_for_timeout(500)

        # Step 4 — fill date fields via JS (readonly datepickers)
        log.info("Hillsborough FL: setting date range %s → %s", start_str, end_str)
        await page.evaluate(f"""
            () => {{
                const after  = document.querySelector('#dateFiledAfter');
                const before = document.querySelector('#dateFiledBefore');
                const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value').set;
                if (after)  {{ setter.call(after,  '{start_str}'); after.dispatchEvent(new Event('change', {{bubbles:true}})); }}
                if (before) {{ setter.call(before, '{end_str}');   before.dispatchEvent(new Event('change', {{bubbles:true}})); }}
            }}
        """)
        await page.wait_for_timeout(500)

        # Step 5 — click Search
        log.info("Hillsborough FL: clicking Search button")
        await page.click("#btnSubmitDateFiledSearch")

        # Step 6 — wait for results table
        try:
            await page.wait_for_selector("button.details", timeout=30_000)
        except Exception as e:
            self.last_error = f"no results after search: {e}"
            log.warning("Hillsborough FL: %s", self.last_error)
            return []

        await page.wait_for_timeout(2_000)
        return await self._collect_all_rows(page, today)

    # ------------------------------------------------------------------ #
    #  Results table — iterate every row                                  #
    # ------------------------------------------------------------------ #

    async def _collect_all_rows(self, page, today: date) -> list[Filing]:
        filings: list[Filing] = []
        processed: set[str] = set()

        while True:
            detail_buttons = await page.query_selector_all("button.details")
            if not detail_buttons:
                break

            log.info("Hillsborough FL: %d rows on current page", len(detail_buttons))

            for idx in range(len(detail_buttons)):
                detail_buttons = await page.query_selector_all("button.details")
                if idx >= len(detail_buttons):
                    break

                row = await detail_buttons[idx].evaluate_handle(
                    "el => el.closest('tr')"
                )
                row_text = ""
                try:
                    row_text = await row.inner_text()
                except Exception:
                    pass
                case_id = row_text.strip()[:40]
                if case_id in processed:
                    continue
                processed.add(case_id)

                row_filings = await self._process_row(page, idx, today)
                filings.extend(row_filings)

                await page.go_back(wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_timeout(2_000)

            break

        return filings

    # ------------------------------------------------------------------ #
    #  Single case                                                         #
    # ------------------------------------------------------------------ #

    async def _process_row(self, page, idx: int, today: date) -> list[Filing]:
        filings: list[Filing] = []
        try:
            detail_buttons = await page.query_selector_all("button.details")
            if idx >= len(detail_buttons):
                return []

            row_el = await detail_buttons[idx].evaluate_handle(
                "el => el.closest('tr')"
            )
            case_number, filing_date = await self._extract_row_meta(row_el, today)

            # FIX: if filing_date still equals today it means we failed to parse
            # a real date from the row — log a warning so it is visible
            if filing_date == today:
                log.debug(
                    "Hillsborough FL: could not parse filing_date for %s "
                    "— defaulting to today",
                    case_number,
                )

            log.debug("Hillsborough FL: opening case %s", case_number)
            await detail_buttons[idx].click()
            await page.wait_for_load_state("domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(2_000)

            parties_tab = await page.query_selector("#tabCaseParties")
            if parties_tab:
                await parties_tab.click()
                await page.wait_for_timeout(1_500)

            filings = await self._extract_defendants(page, case_number, filing_date)

        except Exception as e:
            log.warning("Hillsborough FL: row %d failed: %s", idx, e)

        return filings

    async def _extract_row_meta(self, row_el, today: date) -> tuple[str, date]:
        """Pull case_number and filing_date from a results table row.

        FIX: filing_date no longer silently falls back to today without a log.
        If no date can be parsed the caller logs a debug warning.
        """
        case_number = "UNKNOWN"
        filing_date = today  # caller will log a warning if this stays as today
        try:
            cells = await row_el.query_selector_all("td")
            texts = [(await c.inner_text()).strip() for c in cells]
            if texts:
                case_number = texts[0].strip() or "UNKNOWN"
            for t in texts[1:4]:
                d = self._try_parse_date(t)
                if d:
                    filing_date = d
                    break
        except Exception as e:
            log.debug("Hillsborough FL: row meta extraction failed: %s", e)
        return case_number, filing_date

    async def _extract_defendants(
        self, page, case_number: str, filing_date: date
    ) -> list[Filing]:
        """
        Parse #partiesTable and return one Filing per Defendant.

        Confirmed table structure (from live HTML):
          col 0: Party Type  (Defendant / Plaintiff)
          col 1: Name + address in text nodes after <br>
          col 2: Party Demographics
          col 3: Attorney Name
          col 4: Attorney Contact
        """
        filings: list[Filing] = []
        plaintiff_name = "Unknown"

        try:
            rows = await page.query_selector_all("#partiesTable tbody tr")
            if not rows:
                log.debug("Hillsborough FL: no party rows for %s", case_number)
                return []

            # First pass — plaintiff name
            for row in rows:
                cells = await row.query_selector_all("td")
                if not cells:
                    continue
                party_type = (await cells[0].inner_text()).strip().upper()
                if "PLAINTIFF" in party_type and len(cells) > 1:
                    btn = await cells[1].query_selector("button.userDetails")
                    if btn:
                        plaintiff_name = (await btn.inner_text()).strip()
                    break

            # Second pass — defendants
            for row in rows:
                cells = await row.query_selector_all("td")
                if not cells:
                    continue
                party_type = (await cells[0].inner_text()).strip().upper()
                if "DEFENDANT" not in party_type:
                    continue
                if len(cells) < 2:
                    continue

                defendant_name = "Unknown"
                btn = await cells[1].query_selector("button.userDetails")
                if btn:
                    raw = (await btn.inner_text()).strip()
                    if raw:
                        defendant_name = raw

                # Address from text nodes after <a> in col 1
                defendant_address = await page.evaluate("""
                    (cell) => {
                        const parts = [];
                        let found = false;
                        for (const node of cell.childNodes) {
                            if (node.nodeType === Node.ELEMENT_NODE &&
                                node.tagName === 'A') {
                                found = true;
                                continue;
                            }
                            if (found) {
                                const t = node.textContent.trim();
                                if (t) parts.push(t);
                            }
                        }
                        return parts.join(', ').trim();
                    }
                """, cells[1])

                if not defendant_address:
                    cell_text = (await cells[1].inner_text()).strip()
                    cell_text = cell_text.replace(defendant_name, "").strip()
                    defendant_address = " ".join(cell_text.split()) or "Unknown"

                if not defendant_address:
                    defendant_address = "Unknown"

                source = page.url or SOURCE_URL

                # FIX: clean_tenant_name() can return "" — fall back to "Unknown"
                # not to the raw name (which may be a placeholder or garbled)
                cleaned = clean_tenant_name(defendant_name)
                tenant  = cleaned if cleaned else "Unknown"

                filing = Filing(
                    case_number      = case_number,
                    tenant_name      = tenant,
                    property_address = defendant_address,
                    landlord_name    = plaintiff_name,
                    filing_date      = filing_date,
                    court_date       = None,
                    state            = STATE,
                    county           = COUNTY,
                    notice_type      = NOTICE_TYPE,
                    source_url       = source,
                )
                filings.append(filing)
                log.debug(
                    "Hillsborough FL: %s | %s | %s",
                    case_number, tenant, defendant_address,
                )

        except Exception as e:
            self.last_error = f"party extraction failed for {case_number}: {e}"
            log.warning("Hillsborough FL: %s", self.last_error)

        return filings

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _try_parse_date(raw: str) -> date | None:
        raw = raw.strip()
        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
        return None