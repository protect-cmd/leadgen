from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

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
# - CaseType: #caseTypes     → multi-select, value contains all LANDLORD/TENANT/EVICTION codes
# - DateAfter:  #dateFiledAfter   (readonly datepicker — must use JS to set value)
# - DateBefore: #dateFiledBefore  (readonly datepicker — must use JS to set value)
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
      1. Load case search page
      2. Click date-range tab
      3. Set Case Category = CV (CIVIL)
      4. Select LANDLORD/TENANT/EVICTION case type (multi-select)
      5. Set date range: today-lookback_days → today
      6. Click Search
      7. Wait for results table
      8. For each row: click magnifying-glass → Parties tab → extract defendants
      9. Return Filing list
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
            log.info("Hillsborough FL: loading case search page")
            await page.goto(CASE_SEARCH_URL, wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(3_000)

            filings = await self._run_search(page, start, today)
        except Exception as e:
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

        # Step 1 — click the "Search by date range / court type / case type" tab
        log.info("Hillsborough FL: clicking date-range tab")
        await page.click("#nav-DateFiled-tab")
        await page.wait_for_timeout(1_000)

        # Step 2 — set Case Category to CIVIL
        log.info("Hillsborough FL: selecting CIVIL category")
        await page.select_option("#caseCategory", value="CV")
        await page.wait_for_timeout(500)

        # Step 3 — select LANDLORD/TENANT/EVICTION in the multi-select
        # The <select multiple> uses value strings. We inject JS to select
        # all matching options because Playwright select_option() requires
        # exact value match for each individual option and these are
        # comma-separated compound values in the HTML.
        log.info("Hillsborough FL: selecting eviction case type")
        await page.evaluate("""
            () => {
                const sel = document.querySelector('#caseTypes');
                if (!sel) return;
                // Deselect all first
                for (const opt of sel.options) opt.selected = false;
                // Select the LANDLORD/TENANT/EVICTION option
                const target = Array.from(sel.options).find(o =>
                    o.text.toUpperCase().includes('LANDLORD') ||
                    o.text.toUpperCase().includes('EVICTION')
                );
                if (target) target.selected = true;
                // Fire change event so AngularJS / KO bindings update
                sel.dispatchEvent(new Event('change', {bubbles: true}));
            }
        """)
        await page.wait_for_timeout(500)

        # Step 4 — fill date fields (they are readonly datepickers, use JS)
        log.info("Hillsborough FL: setting date range %s → %s", start_str, end_str)
        await page.evaluate(f"""
            () => {{
                const after  = document.querySelector('#dateFiledAfter');
                const before = document.querySelector('#dateFiledBefore');
                const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value').set;
                if (after)  {{ nativeInputValueSetter.call(after,  '{start_str}'); after.dispatchEvent(new Event('change', {{bubbles:true}})); }}
                if (before) {{ nativeInputValueSetter.call(before, '{end_str}');   before.dispatchEvent(new Event('change', {{bubbles:true}})); }}
            }}
        """)
        await page.wait_for_timeout(500)

        # Step 5 — click Search
        log.info("Hillsborough FL: clicking Search button")
        await page.click("#btnSubmitDateFiledSearch")

        # Step 6 — wait for results table to populate
        try:
            await page.wait_for_selector("button.details", timeout=30_000)
        except Exception:
            log.warning("Hillsborough FL: no results loaded after search")
            return []

        await page.wait_for_timeout(2_000)
        return await self._collect_all_rows(page, today)

    # ------------------------------------------------------------------ #
    #  Results table — iterate every row                                  #
    # ------------------------------------------------------------------ #

    async def _collect_all_rows(self, page, today: date) -> list[Filing]:
        filings: list[Filing] = []
        processed = set()

        while True:
            detail_buttons = await page.query_selector_all("button.details")
            if not detail_buttons:
                break

            log.info("Hillsborough FL: %d rows on current page", len(detail_buttons))

            for idx in range(len(detail_buttons)):
                # Re-query after each navigation back
                detail_buttons = await page.query_selector_all("button.details")
                if idx >= len(detail_buttons):
                    break

                # Get case number from the row to skip already-seen rows
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

                # Navigate back to results
                await page.go_back(wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_timeout(2_000)

            # Pagination: HOVER typically returns all results in one page
            # (500-row max). No next-page button needed for the standard run.
            break

        return filings

    # ------------------------------------------------------------------ #
    #  Single case — click detail, click Parties tab, extract defendants  #
    # ------------------------------------------------------------------ #

    async def _process_row(self, page, idx: int, today: date) -> list[Filing]:
        """Click the magnifying-glass for row[idx], extract parties, go back."""
        filings: list[Filing] = []

        try:
            detail_buttons = await page.query_selector_all("button.details")
            if idx >= len(detail_buttons):
                return []

            # Grab case_number and filing_date from the row before navigating
            row_el = await detail_buttons[idx].evaluate_handle(
                "el => el.closest('tr')"
            )
            case_number, filing_date = await self._extract_row_meta(row_el, today)

            log.debug("Hillsborough FL: opening case %s", case_number)
            await detail_buttons[idx].click()
            await page.wait_for_load_state("domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(2_000)

            # Click Parties tab
            parties_tab = await page.query_selector("#tabCaseParties")
            if parties_tab:
                await parties_tab.click()
                await page.wait_for_timeout(1_500)

            # Extract defendants from #partiesTable
            filings = await self._extract_defendants(
                page, case_number, filing_date
            )

        except Exception as e:
            log.warning("Hillsborough FL: row %d failed: %s", idx, e)

        return filings

    async def _extract_row_meta(self, row_el, today: date) -> tuple[str, date]:
        """Pull case_number and filing_date from a results table row."""
        case_number  = "UNKNOWN"
        filing_date  = today
        try:
            cells = await row_el.query_selector_all("td")
            texts = []
            for c in cells:
                t = (await c.inner_text()).strip()
                texts.append(t)
            # Typical HOVER columns: Case Number | File Date | Case Type | Status
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
        filings      : list[Filing] = []
        plaintiff_name               = "Unknown"

        try:
            rows = await page.query_selector_all("#partiesTable tbody tr")
            if not rows:
                log.debug("Hillsborough FL: no party rows for %s", case_number)
                return []

            # First pass — find plaintiff name
            for row in rows:
                cells = await row.query_selector_all("td")
                if not cells:
                    continue
                party_type = (await cells[0].inner_text()).strip().upper()
                if "PLAINTIFF" in party_type and len(cells) > 1:
                    # Name is in the button text inside col 1
                    btn = await cells[1].query_selector("button.userDetails")
                    if btn:
                        plaintiff_name = (await btn.inner_text()).strip()
                    break

            # Second pass — extract each defendant
            for row in rows:
                cells = await row.query_selector_all("td")
                if not cells:
                    continue
                party_type = (await cells[0].inner_text()).strip().upper()
                if "DEFENDANT" not in party_type:
                    continue
                if len(cells) < 2:
                    continue

                # Defendant name — inside button.userDetails
                defendant_name = "Unknown"
                btn = await cells[1].query_selector("button.userDetails")
                if btn:
                    defendant_name = (await btn.inner_text()).strip()

                # Defendant address — text nodes after the <br> tags in col 1
                # The HTML looks like:
                #   <td>...<button>Name</button><br>8507 WHITE POPLAR DRIVE<br>RIVERVIEW, FL 33578</td>
                defendant_address = await page.evaluate("""
                    (cell) => {
                        // Collect text from all text nodes that are direct
                        // children of the td (after the <a>/<button> element)
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
                    # Fallback: grab all text from cell, strip the name
                    cell_text = (await cells[1].inner_text()).strip()
                    cell_text = cell_text.replace(defendant_name, "").strip()
                    defendant_address = " ".join(cell_text.split()) or "Unknown"

                if not defendant_address:
                    defendant_address = "Unknown"

                source = page.url or SOURCE_URL

                filing = Filing(
                    case_number      = case_number,
                    tenant_name      = clean_tenant_name(defendant_name) or defendant_name,
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
                    case_number, defendant_name, defendant_address
                )

        except Exception as e:
            log.warning(
                "Hillsborough FL: party extraction failed for %s: %s",
                case_number, e
            )

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