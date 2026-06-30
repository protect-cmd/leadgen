from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, timedelta
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from models.filing import Filing
from scrapers.base_scraper import BaseScraper
from scrapers.dates import court_today
from services.name_utils import clean_tenant_name

log = logging.getLogger(__name__)

# Volusia County Clerk publishes a static "New County Daily Suits Report" — a
# plain HTML table of every county-civil suit filed each day, with a Category
# column that labels evictions explicitly. No portal, no captcha, no login.
#
#   Index (lists available day codes): /cm_rpt/inquirySU.aspx?ty=CO
#   Day report:  /cm_rpt/su_county/DayCoALLNew_YYYY_MM_DD.html
#
# Report columns: Case Number | Div | Primary Litigant #1 (plaintiff/landlord,
# with a trailing mailing ZIP) | Primary Litigant #2 (defendant/tenant) |
# Category (e.g. "Eviction", "Small Claims ...").
#
# Property addresses are NOT on the daily suits report; they come from the
# CCMS (Clerk Case Management System) case detail page.  For each eviction case
# we POST a search to the CCMS public case search, then parse the defendant's
# address from the case detail.  This is the v2 per-case lookup described in
# the PR.
#
#   CCMS search: POST https://ccms.clerk.org/CaseSearch.aspx
#   CCMS detail: https://ccms.clerk.org/CaseDetail.aspx?CaseID=<id>
#
# The defendant (tenant) address on CCMS is the property address for eviction
# filings (the service address = the rental unit).
BASE_URL    = "https://app02.clerk.org/cm_rpt"
INDEX_URL   = f"{BASE_URL}/inquirySU.aspx?ty=CO"
DAY_URL_FMT = BASE_URL + "/su_county/DayCoALLNew_{y:04d}_{m:02d}_{d:02d}.html"

CCMS_SEARCH_URL = "https://ccms.clerk.org/CaseSearch.aspx"
CCMS_DETAIL_URL = "https://ccms.clerk.org/CaseDetail.aspx"

STATE          = "FL"
COUNTY         = "Volusia"
COURT_TIMEZONE = "America/New_York"
NOTICE_TYPE    = "Eviction"

# Max concurrent per-case CCMS fetches to avoid hammering the server.
_CCMS_CONCURRENCY = 5

_DAY_CODE_RE    = re.compile(r"DayCoALLNew_(\d{4})_(\d{2})_(\d{2})\.html")
_CASE_NUM_RE    = re.compile(r"^\d{4}\s+\d+\s+\w+")
_TRAILING_ZIP_RE = re.compile(r"[\s,]*\b\d{5}(?:-\d{4})?\s*$")
# CCMS case IDs appear as numeric query-string values in detail page links.
_CASE_ID_RE     = re.compile(r"CaseDetail\.aspx\?CaseID=(\d+)", re.IGNORECASE)
# Match "123 MAIN ST, DAYTONA BEACH, FL 32114" style addresses in CCMS HTML.
_ADDRESS_RE     = re.compile(
    r"\b\d+\s+[A-Z][A-Z0-9 .#'-]*,\s*[A-Z][A-Z ]+,\s*FL\s+\d{5}",
    re.IGNORECASE,
)


class VolusiaScraper(BaseScraper):
    """
    Scrapes Volusia County's New County Daily Suits Report for evictions, then
    enriches each filing with the property address from the CCMS case detail.
    """

    def __init__(self, lookback_days: int = 2, headless: bool = True):
        super().__init__(headless=headless)
        self.lookback_days = lookback_days
        self.last_error: Optional[str] = None

    async def scrape(self) -> list[Filing]:
        self.last_error = None
        today = court_today(COURT_TIMEZONE)
        start = today - timedelta(days=self.lookback_days)
        filings: list[Filing] = []

        async with httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; leadgen/1.0)"},
        ) as client:
            try:
                report_dates = await self._available_dates(client, start, today)
            except Exception as e:
                self.last_error = f"index fetch failed: {e}"
                log.error("Volusia FL: %s", self.last_error, exc_info=True)
                return []

            log.info("Volusia FL: %d daily reports in window", len(report_dates))
            for d in report_dates:
                filings.extend(await self._scrape_day(client, d))

        if not filings:
            # Zero filings after a successful fetch is suspicious — could be a
            # silent block (geo-block, structural change).  Flag it.
            self.last_error = "zero evictions returned; possible block or empty window"
            log.warning("Volusia FL: %s", self.last_error)
            return []

        # De-dupe by case number across overlapping reports.
        unique: dict[str, Filing] = {f.case_number: f for f in filings}

        # Enrich addresses via CCMS in parallel (best-effort — falls back to
        # "Unknown" on any per-case error so the filing is not dropped).
        enriched = await self._enrich_addresses(list(unique.values()))

        log.info("Volusia FL: %d eviction filings found", len(enriched))
        return enriched

    # ------------------------------------------------------------------ #
    #  Report discovery + day fetching                                     #
    # ------------------------------------------------------------------ #

    async def _available_dates(
        self, client: httpx.AsyncClient, start: date, today: date
    ) -> list[date]:
        """Read the index for published day codes; raises on fetch failure."""
        r = await client.get(INDEX_URL)
        r.raise_for_status()
        dates = sorted(
            {
                date(int(y), int(m), int(d))
                for y, m, d in _DAY_CODE_RE.findall(r.text)
            },
            reverse=True,
        )
        in_window = [d for d in dates if start <= d <= today]
        if in_window:
            return in_window

        # Index parsed OK but no dates in window — fall back to calendar range.
        log.info("Volusia FL: no index entries in window; using date range fallback")
        span = (today - start).days
        return [today - timedelta(days=i) for i in range(span + 1)]

    async def _scrape_day(self, client: httpx.AsyncClient, d: date) -> list[Filing]:
        url = DAY_URL_FMT.format(y=d.year, m=d.month, d=d.day)
        try:
            r = await client.get(url)
            if r.status_code == 404:
                return []
            r.raise_for_status()
        except Exception as e:
            log.warning("Volusia FL: day %s fetch failed: %s", d.isoformat(), e)
            return []

        filings = self._parse_report(r.text, d)
        log.info("Volusia FL: %s — %d evictions", d.isoformat(), len(filings))
        return filings

    # ------------------------------------------------------------------ #
    #  Parsing (pure / unit-tested)                                        #
    # ------------------------------------------------------------------ #

    @classmethod
    def _parse_report(cls, html: str, report_date: date) -> list[Filing]:
        soup = BeautifulSoup(html, "html.parser")
        filings: list[Filing] = []

        for tr in soup.select("tr"):
            cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
            if len(cells) < 5 or not _CASE_NUM_RE.match(cells[0]):
                continue

            case_raw, _div, litigant1, litigant2, category = cells[:5]
            if "evict" not in category.lower():
                continue

            landlord = cls._clean_litigant(litigant1)
            tenant_raw = cls._clean_litigant(litigant2)
            tenant = clean_tenant_name(tenant_raw) or "Unknown"

            filings.append(
                Filing(
                    case_number      = cls._normalize_case_number(case_raw),
                    tenant_name      = tenant,
                    property_address = "Unknown",
                    landlord_name    = landlord or "Unknown",
                    filing_date      = report_date,
                    court_date       = None,
                    state            = STATE,
                    county           = COUNTY,
                    notice_type      = NOTICE_TYPE,
                    source_url       = DAY_URL_FMT.format(
                        y=report_date.year, m=report_date.month, d=report_date.day
                    ),
                )
            )
        return filings

    @staticmethod
    def _clean_litigant(raw: str) -> str:
        text = re.sub(r"\s+", " ", raw or "").strip()
        text = _TRAILING_ZIP_RE.sub("", text).strip()
        return text

    @staticmethod
    def _normalize_case_number(raw: str) -> str:
        # "2026 20582 COCI" -> "2026-20582-COCI"
        return re.sub(r"\s+", "-", raw.strip())

    # ------------------------------------------------------------------ #
    #  Per-case CCMS address enrichment                                    #
    # ------------------------------------------------------------------ #

    async def _enrich_addresses(self, filings: list[Filing]) -> list[Filing]:
        """Look up each case on CCMS and back-fill property_address."""
        sem = asyncio.Semaphore(_CCMS_CONCURRENCY)

        async with httpx.AsyncClient(
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; leadgen/1.0)"},
        ) as client:
            tasks = [self._fetch_address(client, sem, f) for f in filings]
            enriched = await asyncio.gather(*tasks)

        found = sum(1 for f in enriched if f.property_address != "Unknown")
        log.info(
            "Volusia FL: CCMS address lookup — %d/%d resolved", found, len(enriched)
        )
        return list(enriched)

    async def _fetch_address(
        self,
        client: httpx.AsyncClient,
        sem: asyncio.Semaphore,
        filing: Filing,
    ) -> Filing:
        async with sem:
            address = await self._ccms_address(client, filing.case_number)
        if address:
            return Filing(
                case_number      = filing.case_number,
                tenant_name      = filing.tenant_name,
                property_address = address,
                landlord_name    = filing.landlord_name,
                filing_date      = filing.filing_date,
                court_date       = filing.court_date,
                state            = filing.state,
                county           = filing.county,
                notice_type      = filing.notice_type,
                source_url       = filing.source_url,
            )
        return filing

    async def _ccms_address(
        self, client: httpx.AsyncClient, case_number: str
    ) -> Optional[str]:
        """
        Search CCMS by case number and return the defendant's street address,
        or None if not found / parse fails.

        CCMS public search: https://ccms.clerk.org/CaseSearch.aspx
        We POST the case number, follow the redirect to the detail page, then
        parse the Parties section for the defendant address.
        """
        try:
            # Step 1: load the search page to get the ASP.NET viewstate tokens.
            r = await client.get(CCMS_SEARCH_URL)
            r.raise_for_status()
            viewstate, eventval, generator = _parse_aspnet_hidden(r.text)

            # Step 2: POST search by case number.
            # The CCMS search form field name is typically "CaseNumber" or
            # "txtCaseNumber" — we match what the server provides in the HTML.
            form_data = {
                "__VIEWSTATE":          viewstate,
                "__EVENTVALIDATION":    eventval,
                "__VIEWSTATEGENERATOR": generator,
                "txtCaseNumber":        case_number,
                "btnSearch":            "Search",
            }
            r2 = await client.post(CCMS_SEARCH_URL, data=form_data)
            r2.raise_for_status()

            # If the search page itself contains a CaseDetail link, follow it.
            detail_url = _extract_first_detail_url(r2.text)
            if not detail_url:
                log.debug("Volusia CCMS: no case detail found for %s", case_number)
                return None

            r3 = await client.get(detail_url)
            r3.raise_for_status()
            return _parse_defendant_address(r3.text)

        except Exception as e:
            log.debug("Volusia CCMS: address lookup failed for %s: %s", case_number, e)
            return None


# ------------------------------------------------------------------ #
#  CCMS HTML helpers (module-level, pure / unit-testable)             #
# ------------------------------------------------------------------ #

def _parse_aspnet_hidden(html: str) -> tuple[str, str, str]:
    """Extract ASP.NET hidden field values needed for form POST."""
    soup = BeautifulSoup(html, "html.parser")

    def _val(name: str) -> str:
        tag = soup.find("input", {"name": name})
        return tag["value"] if tag else ""

    return (
        _val("__VIEWSTATE"),
        _val("__EVENTVALIDATION"),
        _val("__VIEWSTATEGENERATOR"),
    )


def _extract_first_detail_url(html: str) -> Optional[str]:
    """Return the first CaseDetail.aspx URL from a CCMS search results page."""
    m = _CASE_ID_RE.search(html)
    if not m:
        return None
    return f"{CCMS_DETAIL_URL}?CaseID={m.group(1)}"


def _parse_defendant_address(html: str) -> Optional[str]:
    """
    Parse the CCMS case detail page for the defendant's address.

    CCMS detail pages list parties in a table.  We look for the row labelled
    "Defendant" (or "Respondent") and take the address from the adjacent cell.
    Falls back to a broad regex scan over the full page if table parsing fails.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Primary: scan party rows for Defendant/Respondent + address cell.
    for row in soup.find_all("tr"):
        cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
        if len(cells) < 2:
            continue
        role = cells[0].strip().upper()
        if role in ("DEFENDANT", "RESPONDENT"):
            # Address is commonly in the 3rd or 4th cell depending on layout.
            for cell in cells[1:]:
                addr = _extract_address(cell)
                if addr:
                    return addr

    # Fallback: search all visible text for an address pattern.
    text = soup.get_text(" ")
    return _extract_address(text)


def _extract_address(text: str) -> Optional[str]:
    """Return the first FL street address found in text, normalised."""
    m = _ADDRESS_RE.search(text)
    if not m:
        return None
    # Collapse internal whitespace.
    return re.sub(r"\s+", " ", m.group(0)).strip()
