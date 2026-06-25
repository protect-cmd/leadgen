from __future__ import annotations

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
# column that labels evictions explicitly. No portal, no captcha, no login:
# the day reports are fetchable directly over HTTP, so this scraper is pure
# httpx + BeautifulSoup (no browser).
#
#   Index (lists available day codes): /cm_rpt/inquirySU.aspx?ty=CO
#   Day report:  /cm_rpt/su_county/DayCoALLNew_YYYY_MM_DD.html
#
# Report columns: Case Number | Div | Primary Litigant #1 (plaintiff/landlord,
# with a trailing mailing ZIP) | Primary Litigant #2 (defendant/tenant) |
# Category (e.g. "Eviction", "Small Claims ...").
#
# The report carries no property address (only the landlord's mailing ZIP), so
# filings are emitted with property_address="Unknown"; the tenant-track
# enrichment handles no-address leads. (Per-case address lookup via ccms.clerk.org
# is a possible future enhancement.)
BASE_URL    = "https://app02.clerk.org/cm_rpt"
INDEX_URL   = f"{BASE_URL}/inquirySU.aspx?ty=CO"
DAY_URL_FMT = BASE_URL + "/su_county/DayCoALLNew_{y:04d}_{m:02d}_{d:02d}.html"

STATE          = "FL"
COUNTY         = "Volusia"
COURT_TIMEZONE = "America/New_York"
NOTICE_TYPE    = "Eviction"

_DAY_CODE_RE = re.compile(r"DayCoALLNew_(\d{4})_(\d{2})_(\d{2})\.html")
_CASE_NUM_RE = re.compile(r"^\d{4}\s+\d+\s+\w+")
_TRAILING_ZIP_RE = re.compile(r"[\s,]*\b\d{5}(?:-\d{4})?\s*$")


class VolusiaScraper(BaseScraper):
    """
    Scrapes Volusia County's New County Daily Suits Report for evictions.

    Pure HTTP — fetches each day's static report in the lookback window and
    keeps rows whose Category is "Eviction".
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

        try:
            async with httpx.AsyncClient(
                verify=False,  # app02.clerk.org reports have cert quirks
                timeout=30,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; leadgen/1.0)"},
            ) as client:
                report_dates = await self._available_dates(client, start, today)
                log.info("Volusia FL: %d daily reports in window", len(report_dates))
                for d in report_dates:
                    filings.extend(await self._scrape_day(client, d))
        except Exception as e:
            self.last_error = str(e)
            log.error("Volusia FL: scrape failed: %s", e, exc_info=True)

        # De-dupe by case number across overlapping reports.
        unique = {f.case_number: f for f in filings}
        result = list(unique.values())
        log.info("Volusia FL: %d eviction filings found", len(result))
        return result

    async def _available_dates(
        self, client: httpx.AsyncClient, start: date, today: date
    ) -> list[date]:
        """Read the index for published day codes, falling back to a plain
        date range if the index can't be parsed."""
        try:
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
        except Exception as e:
            log.warning("Volusia FL: index fetch failed (%s) — falling back to date range", e)

        # Fallback: try every calendar day in the window (404s are skipped).
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
            tenant = clean_tenant_name(tenant_raw) or tenant_raw or "Unknown"

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
        """Collapse whitespace and strip the trailing mailing ZIP the report
        appends to litigant names."""
        text = re.sub(r"\s+", " ", raw or "").strip()
        text = _TRAILING_ZIP_RE.sub("", text).strip()
        return text

    @staticmethod
    def _normalize_case_number(raw: str) -> str:
        # "2026 20582 COCI" -> "2026-20582-COCI"
        return re.sub(r"\s+", "-", raw.strip())
