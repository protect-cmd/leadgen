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

# Duval County (Jacksonville) Clerk of Courts — CORE (Clerk Online Resource
# ePortal). Public Access requires no login or captcha; the only quirk is an
# intermittently-expired TLS cert (we launch with ignore_https_errors=True).
#
# Confirmed from live DOM inspection (June 2026):
#   - Landing loads straight into "Public Access" mode.
#   - Left nav "Case Search" (a <td onclick="openCmsPage()">) opens the search
#     form in an Infragistics WebTab.
#   - Search form (IDs carry a per-session GUID suffix, so match by substring):
#       Court Type : select[id*='CourtTypeDropDownList']  -> "County Civil"
#       Case Type  : select[id*='CaseTypeDropDownList']   -> "Eviction"
#       Case Year  : input[id*='UcnYearTextBox']
#       Submit     : input[value='Begin Search']
#   - Results render newest-first by case number; the list shows no filing date,
#     so we open each case (double-click the row) and read "File Date" + the
#     Parties section (defendant address = the property) off the detail tab,
#     then switch back to the "Search Results" tab.
#   - Pager: input[id*='NextPageButton'] (value ">").
PORTAL_URL     = "https://core.duvalclerk.com/CoreCms.aspx?mode=PublicAccess"
SOURCE_URL     = PORTAL_URL
STATE          = "FL"
COUNTY         = "Duval"
COURT_TIMEZONE = "America/New_York"
NOTICE_TYPE    = "Residential Eviction"

COURT_TYPE_LABEL = "County Civil"
CASE_TYPE_LABEL  = "Eviction"

_PARTY_TYPES = ("PLAINTIFF", "DEFENDANT", "PETITIONER", "RESPONDENT")
_FILE_DATE_RE = re.compile(r"File Date\s+(\d{1,2}/\d{1,2}/\d{4})")
_PARTY_SKIP = {"Name / DOB / DL / ID #", "Party Type", "Race / Sex", "Address"}

# Ordered, de-duplicated list of visible case numbers in the results grid.
_CASES_JS = r"""() => {
  const out = [], seen = new Set();
  for (const e of document.querySelectorAll('td,div,span')) {
    if (e.offsetParent === null) continue;
    const m = (e.innerText || '').trim().match(/^(20\d\d-CC-\d{6}-\w+)$/);
    if (m && !seen.has(m[1])) { seen.add(m[1]); out.push(m[1]); }
  }
  return out;
}"""

_FIRST_CASE_JS = r"""() => {
  const e = [...document.querySelectorAll('td,div,span')]
    .find(x => x.offsetParent !== null && /^20\d\d-CC-\d{6}-\w+$/.test((x.innerText || '').trim()));
  return e ? e.innerText.trim() : null;
}"""

# Click the left-nav "Case Search" item (a non-anchor <td> with onclick).
_OPEN_CASE_SEARCH_NAV_JS = """() => {
  const el = [...document.querySelectorAll('*')]
    .find(e => e.children.length === 0 && (e.innerText || '').trim() === 'Case Search');
  if (el) { (el.closest('a') || el).click(); return true; }
  return false;
}"""

# Best-effort: close any open case-detail tabs so they don't accumulate.
_CLOSE_CASE_TABS_JS = r"""() => {
  for (const t of document.querySelectorAll('.igtab_THTab')) {
    if (/\d{4}-CC-\d/.test((t.innerText || '').trim())) {
      const close = [...t.querySelectorAll('*')].find(c => /Close/i.test(c.className || ''));
      if (close) close.click();
    }
  }
}"""


class DuvalScraper(BaseScraper):
    """
    Scrapes Duval County (Jacksonville) CORE portal for Residential Eviction
    filings. Public Access — no login or captcha.

    Flow per run:
      1. Load CORE Public Access (ignore_https_errors for the expired cert).
      2. Open the Case Search form.
      3. For each relevant year: select County Civil + Eviction, enter the
         year, Begin Search.
      4. Results are newest-first; open each case (double-click), read File
         Date + Parties (defendant = tenant + property address), then return
         to the results tab. Stop once a case is older than the lookback
         window. Paginate with the Next button.
    """

    def __init__(self, lookback_days: int = 2, headless: bool = True, max_pages: int = 15):
        super().__init__(headless=headless)
        self.lookback_days = lookback_days
        self.max_pages = max_pages
        self.last_error: Optional[str] = None

    async def _launch_browser(self):
        # Override base launch to tolerate CORE's intermittently-expired TLS cert.
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
            log.info("Duval FL: loading CORE public access")
            await page.goto(PORTAL_URL, wait_until="networkidle", timeout=60_000)
            await page.wait_for_timeout(3_000)

            # Search each year the lookback window touches (handles Jan rollover).
            for year in sorted({start.year, today.year}, reverse=True):
                filings.extend(await self._search_year(page, year, start, today))
        except Exception as e:
            self.last_error = str(e)
            log.error("Duval FL: scrape failed: %s", e, exc_info=True)
        finally:
            await self._close_browser()

        # De-dupe by case number (a case can surface under multiple party rows).
        unique = {f.case_number: f for f in filings}
        result = list(unique.values())
        log.info("Duval FL: %d filings found", len(result))
        return result

    # ------------------------------------------------------------------ #
    #  Search                                                              #
    # ------------------------------------------------------------------ #

    async def _activate_search_form(self, page) -> bool:
        """Make the Case Search form visible (nav item first run, tab after)."""
        tab = page.locator(".igtab_THTab", has_text="Case Search")
        if await tab.count():
            try:
                await tab.first.click(timeout=5_000)
            except Exception:
                pass
        else:
            await page.evaluate(_OPEN_CASE_SEARCH_NAV_JS)
        try:
            await page.wait_for_selector(
                "select[id*='CaseTypeDropDownList']", state="visible", timeout=30_000
            )
            await page.wait_for_timeout(1_000)
            return True
        except Exception as e:
            self.last_error = f"case search form never appeared: {e}"
            log.warning("Duval FL: %s", self.last_error)
            return False

    async def _search_year(self, page, year: int, start: date, today: date) -> list[Filing]:
        if not await self._activate_search_form(page):
            return []

        log.info("Duval FL: searching %s evictions, year %d", COURT_TYPE_LABEL, year)
        try:
            await page.select_option("select[id*='CourtTypeDropDownList']", label=COURT_TYPE_LABEL)
            await page.wait_for_timeout(400)
            await page.select_option("select[id*='CaseTypeDropDownList']", label=CASE_TYPE_LABEL)
            await page.fill("input[id*='UcnYearTextBox']", str(year))
            await page.wait_for_timeout(300)
            await page.click("input[value='Begin Search']")
        except Exception as e:
            log.warning("Duval FL: search form interaction failed (year %d): %s", year, e)
            return []

        try:
            await page.wait_for_selector(
                ":text-matches('20\\\\d\\\\d-CC-\\\\d{6}'):visible", timeout=30_000
            )
        except Exception:
            log.info("Duval FL: no results for year %d", year)
            return []

        await page.wait_for_timeout(2_000)
        return await self._collect(page, start, today)

    async def _collect(self, page, start: date, today: date) -> list[Filing]:
        filings: list[Filing] = []
        seen: set[str] = set()

        for page_idx in range(1, self.max_pages + 1):
            case_numbers = await page.evaluate(_CASES_JS)
            log.info("Duval FL: results page %d — %d cases", page_idx, len(case_numbers))

            stop = False
            for case_number in case_numbers:
                if case_number in seen:
                    continue
                seen.add(case_number)

                filing, filing_date = await self._open_and_parse(page, case_number)
                if filing_date and filing_date < start:
                    # Newest-first ordering: everything below is older too.
                    stop = True
                    break
                if filing and filing_date and start <= filing_date <= today:
                    filings.append(filing)

            if stop or not await self._goto_next_page(page):
                break

        return filings

    # ------------------------------------------------------------------ #
    #  Single case                                                         #
    # ------------------------------------------------------------------ #

    async def _open_and_parse(self, page, case_number: str) -> tuple[Filing | None, date | None]:
        try:
            await page.locator(":text-is('%s'):visible" % case_number).first.dblclick(timeout=8_000)
        except Exception as e:
            log.debug("Duval FL: could not open %s: %s", case_number, e)
            return None, None

        await page.wait_for_timeout(3_500)
        try:
            detail = await page.inner_text("body")
        except Exception:
            detail = ""
        await self._back_to_results(page)

        parsed = self._parse_case_detail(detail)
        filing_date = parsed["file_date"]
        if not filing_date:
            log.debug("Duval FL: no File Date parsed for %s", case_number)
            return None, None

        # clean_tenant_name returns "" for placeholders (Jane Doe, "all
        # occupants", etc.); fall back to "Unknown" rather than re-injecting the
        # raw placeholder, which would otherwise pass the downstream name gate.
        tenant = clean_tenant_name(parsed["tenant"] or "") or "Unknown"

        filing = Filing(
            case_number      = case_number,
            tenant_name      = tenant,
            property_address = parsed["address"] or "Unknown",
            landlord_name    = parsed["landlord"] or "Unknown",
            filing_date      = filing_date,
            court_date       = None,
            state            = STATE,
            county           = COUNTY,
            notice_type      = NOTICE_TYPE,
            source_url       = SOURCE_URL,
        )
        return filing, filing_date

    async def _back_to_results(self, page) -> None:
        # Close the open case tab so tabs don't grow unbounded, then make sure
        # the Search Results tab is the active one.
        try:
            await page.evaluate(_CLOSE_CASE_TABS_JS)
            await page.wait_for_timeout(600)
        except Exception:
            pass
        try:
            await page.locator(".igtab_THTab", has_text="Search Results").first.click(timeout=5_000)
            await page.wait_for_timeout(1_200)
        except Exception as e:
            log.debug("Duval FL: back-to-results failed: %s", e)

    async def _goto_next_page(self, page) -> bool:
        before = await page.evaluate(_FIRST_CASE_JS)
        btn = await page.query_selector("input[id*='NextPageButton']:not([disabled])")
        if not btn:
            return False
        try:
            await btn.click()
        except Exception:
            return False
        await page.wait_for_timeout(3_000)
        after = await page.evaluate(_FIRST_CASE_JS)
        return bool(after) and after != before

    # ------------------------------------------------------------------ #
    #  Parsing (pure / unit-tested)                                        #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_case_detail(text: str) -> dict:
        """Extract file_date, landlord (plaintiff), tenant + property address
        (defendant) from a CORE case-detail innerText blob."""
        out: dict = {"file_date": None, "landlord": None, "tenant": None, "address": None}

        m = _FILE_DATE_RE.search(text or "")
        if m:
            try:
                out["file_date"] = datetime.strptime(m.group(1), "%m/%d/%Y").date()
            except ValueError:
                pass

        parties = DuvalScraper._parse_parties(text or "")
        plaintiffs = [p for p in parties if p["type"] == "PLAINTIFF"]
        defendants = [p for p in parties if p["type"] == "DEFENDANT"]

        if plaintiffs:
            out["landlord"] = plaintiffs[0]["name"]
        if defendants:
            out["tenant"] = defendants[0]["name"]
            out["address"] = defendants[0]["address"] or (
                plaintiffs[0]["address"] if plaintiffs else None
            )
        elif plaintiffs:
            out["address"] = plaintiffs[0]["address"]
        return out

    @staticmethod
    def _parse_parties(text: str) -> list[dict]:
        if "Parties" not in text:
            return []
        block = text.split("Parties", 1)[1]
        block = re.split(r"\n\s*Attorneys\b", block, maxsplit=1)[0]

        parties: list[dict] = []
        cur: dict | None = None
        for raw in block.splitlines():
            parts = [s.strip() for s in raw.split("\t")]
            ptypes = [s for s in parts if s in _PARTY_TYPES]
            if ptypes:
                if cur:
                    parties.append(cur)
                name = parts[0] if (parts and parts[0] and parts[0] not in _PARTY_TYPES) else ""
                cur = {"name": name, "type": ptypes[0], "addr": []}
                continue

            line = raw.strip()
            if cur is None or not line:
                continue
            if line in _PARTY_SKIP or line.startswith("Name / DOB") or line.startswith("/"):
                continue
            cur["addr"].append(line)

        if cur:
            parties.append(cur)
        for p in parties:
            p["address"] = DuvalScraper._normalize_address(p["addr"])
        return parties

    @staticmethod
    def _normalize_address(lines: list[str]) -> str | None:
        if not lines:
            return None
        addr = ", ".join(l.strip() for l in lines if l.strip())
        addr = re.sub(r"\bFL(\d{5})", r"FL \1", addr)   # "JACKSONVILLE, FL32221" -> "FL 32221"
        addr = re.sub(r"\s+", " ", addr).strip().strip(",").strip()
        return addr or None
