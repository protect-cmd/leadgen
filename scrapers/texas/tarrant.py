from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, timedelta
from typing import Any

from bs4 import BeautifulSoup

from models.filing import Filing
from scrapers.dates import court_today

log = logging.getLogger(__name__)

STATE = "TX"
COUNTY = "Tarrant"
COURT_TIMEZONE = "America/Chicago"
NOTICE_TYPE = "EFile Evictions"

MAIN_URL = "https://odyssey.tarrantcounty.com/PublicAccess/default.aspx"
SEARCH_BASE = "https://portal-txtarrant.tylertech.cloud/PublicAccess"

# All JP Courts (1–8) combined select value
JP_ALL_VALUE = "400,401,402,403,404,405,406,407,408,409"

# Individual JP court values — used when all-courts search hits the 200-row cap
JP_COURT_VALUES = ["401", "402", "403", "404", "405", "406", "407", "408"]

_OCCUPANTS_RE = re.compile(
    r"\s*,?\s*(?:AND\s+ALL\s+(?:OTHER\s+)?OCCUPANTS?|ET\.?\s*AL\.?).*$",
    flags=re.IGNORECASE,
)
_VS_RE = re.compile(r"\s+vs\.\s+", flags=re.IGNORECASE)
_DATE_RE = re.compile(r"(\d{2}/\d{2}/\d{4})")
_TOO_MANY_RE = re.compile(r"too many matches", re.IGNORECASE)


def _bright_data_ws_url() -> str:
    """Return Bright Data Scraping Browser WebSocket URL from env or built-in default."""
    explicit = os.getenv("BRIGHTDATA_SB_WS")
    if explicit:
        return explicit
    customer = os.getenv("BRIGHTDATA_CUSTOMER_ID", "hl_74fc5212")
    zone = os.getenv("BRIGHTDATA_ZONE", "scraping_browser1")
    password = os.getenv("BRIGHTDATA_ZONE_PASSWORD", "db1ticxa4ik3")
    return f"wss://brd-customer-{customer}-zone-{zone}:{password}@brd.superproxy.io:9222"


def _clean_tenant(raw: str) -> str:
    """Strip 'AND ALL OCCUPANTS' and return first named defendant.

    Splits only on comma-without-space to separate multiple defendants
    (e.g. "Phillips,Emerson") while preserving "Last, First" single names.
    """
    cleaned = _OCCUPANTS_RE.sub("", raw).strip()
    first = re.split(r",(?!\s)", cleaned)[0].strip()
    return first or cleaned or "Unknown"


def _parse_style(style: str) -> tuple[str, str]:
    """Split 'Landlord vs. Tenant ...' into (landlord, tenant)."""
    parts = _VS_RE.split(style, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip() or "Unknown", _clean_tenant(parts[1])
    return "Unknown", "Unknown"


def _parse_results_page(html: str) -> list[dict]:
    """
    Parse Tarrant Odyssey Case Records Search results page.

    Returns list of dicts:
        case_id, case_number, landlord, tenant, filing_date, court_location
    Only rows with case type containing "eviction" are returned.
    """
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict] = []

    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 5:
            continue

        case_link = tds[0].find("a", href=re.compile(r"CaseDetail\.aspx\?CaseID=\d+"))
        if not case_link:
            continue

        m = re.search(r"CaseID=(\d+)", case_link.get("href", ""))
        if not m:
            continue

        # Cell index 4: "EFile Evictions\nFiled" etc.
        type_text = tds[4].get_text(" ", strip=True)
        if "eviction" not in type_text.lower():
            continue

        case_id = m.group(1)
        case_number = case_link.get_text(strip=True)

        # Cell 2: style = "Plaintiff vs. Defendant(s) ..."
        style = tds[2].get_text(" ", strip=True)
        landlord, tenant = _parse_style(style)

        # Cell 3: "05/14/2026 JP No. 1"
        date_cell = tds[3].get_text(" ", strip=True)
        dm = _DATE_RE.search(date_cell)
        filing_date: date
        if dm:
            filing_date = datetime.strptime(dm.group(1), "%m/%d/%Y").date()
        else:
            filing_date = date.today()
        location = _DATE_RE.sub("", date_cell).strip()

        results.append(
            {
                "case_id": case_id,
                "case_number": case_number,
                "landlord": landlord,
                "tenant": tenant,
                "filing_date": filing_date,
                "court_location": location,
            }
        )

    return results


def _parse_case_detail(html: str) -> dict:
    """
    Parse Tarrant Odyssey CaseDetail page for defendant address and court date.

    Returns dict: property_address (str), court_date (date | None).
    Uses the first Defendant row that has a recognisable street address.
    """
    soup = BeautifulSoup(html, "html.parser")
    property_address = "Unknown"
    court_date: date | None = None

    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if not tds:
            continue
        role = tds[0].get_text(strip=True).lower()
        if role != "defendant":
            continue

        # Address is in the last td of the party row; strip &nbsp; indentation
        addr_td = tds[-1]
        raw_lines = [
            line.strip().lstrip(" 　 ")
            for line in addr_td.get_text("\n").split("\n")
            if line.strip().lstrip(" 　 ")
        ]
        # Keep lines that look like address parts (contain a digit, or state+zip)
        addr_lines = [
            l for l in raw_lines
            if re.search(r"\d", l) or re.match(r"^[A-Z]{2}\s+\d{5}", l)
        ]
        if addr_lines:
            property_address = ", ".join(addr_lines)
            break

    # Court date: "06/04/2026  Eviction Non-Jury Trial"
    text = soup.get_text("\n")
    ct_match = re.search(
        r"(\d{2}/\d{2}/\d{4})\s+Eviction Non-Jury Trial",
        text,
        re.IGNORECASE,
    )
    if ct_match:
        try:
            court_date = datetime.strptime(ct_match.group(1), "%m/%d/%Y").date()
        except ValueError:
            pass

    return {"property_address": property_address, "court_date": court_date}


class TarrantCountyJPScraper:
    """
    Scrapes Tarrant County TX Justice of the Peace eviction filings via the
    Tyler Odyssey public portal, routed through Bright Data Scraping Browser
    to handle bot-detection transparently.

    green source: exposes tenant name + rental unit address (defendant address
    from CaseDetail matches the eviction property).
    """

    def __init__(self, lookback_days: int = 2, max_cases: int | None = None):
        self.lookback_days = lookback_days
        self.max_cases = max_cases
        self.last_error: str | None = None

    async def scrape(self) -> list[Filing]:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.last_error = "playwright not installed"
            log.error("Tarrant TX: playwright not installed")
            return []

        self.last_error = None
        today = court_today(COURT_TIMEZONE)
        after = today - timedelta(days=self.lookback_days)
        after_str = after.strftime("%m/%d/%Y")
        today_str = today.strftime("%m/%d/%Y")

        filings: list[Filing] = []
        seen_cases: set[str] = set()

        async with async_playwright() as p:
            try:
                ws_url = _bright_data_ws_url()
                log.info("Tarrant TX: connecting to Bright Data Scraping Browser")
                browser = await p.chromium.connect_over_cdp(ws_url)

                try:
                    eviction_rows = await self._search_evictions(
                        browser,
                        court_value=JP_ALL_VALUE,
                        after_str=after_str,
                        today_str=today_str,
                    )

                    # If the portal capped at 200 and warned "too many matches",
                    # fall back to searching each JP court individually.
                    if eviction_rows is None:
                        log.info(
                            "Tarrant TX: all-courts search hit 200 cap, "
                            "falling back to per-court search"
                        )
                        eviction_rows = []
                        for court_val in JP_COURT_VALUES:
                            rows = await self._search_evictions(
                                browser,
                                court_value=court_val,
                                after_str=after_str,
                                today_str=today_str,
                            )
                            if rows:
                                eviction_rows.extend(rows)

                    log.info(
                        "Tarrant TX: %d eviction rows found, fetching case details",
                        len(eviction_rows),
                    )

                    page = await browser.new_page()
                    for row in eviction_rows:
                        if self.max_cases and len(filings) >= self.max_cases:
                            break
                        if row["case_number"] in seen_cases:
                            continue

                        detail = await self._fetch_case_detail(page, row["case_id"])
                        seen_cases.add(row["case_number"])

                        detail_url = (
                            f"{SEARCH_BASE}/CaseDetail.aspx?CaseID={row['case_id']}"
                        )
                        filings.append(
                            Filing(
                                case_number=row["case_number"],
                                tenant_name=row["tenant"],
                                property_address=detail["property_address"],
                                landlord_name=row["landlord"],
                                filing_date=row["filing_date"],
                                court_date=detail["court_date"],
                                state=STATE,
                                county=COUNTY,
                                notice_type=NOTICE_TYPE,
                                source_url=detail_url,
                            )
                        )
                    await page.close()

                finally:
                    await browser.close()

            except Exception as e:
                self.last_error = str(e)
                log.error("Tarrant TX: scrape error: %s", e, exc_info=True)

        log.info("Tarrant TX: %d eviction filings found", len(filings))
        return filings

    async def _search_evictions(
        self,
        browser: Any,
        *,
        court_value: str,
        after_str: str,
        today_str: str,
    ) -> list[dict] | None:
        """
        Open a search page, filter by Date Filed, parse eviction rows.
        Returns None if the portal hit the 200-row cap ("too many matches").
        """
        page = await browser.new_page()
        try:
            await page.goto(MAIN_URL, timeout=45_000, wait_until="domcontentloaded")
            await page.wait_for_timeout(1_500)

            await page.select_option("#sbxControlID2", court_value)
            await page.evaluate(
                "LaunchSearch('Search.aspx?ID=200', false, true, sbxControlID2)"
            )
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(2_000)

            await page.select_option("#SearchBy", "6")  # Date Filed
            await page.wait_for_timeout(2_000)

            await page.fill("#DateFiledOnAfter", after_str)
            await page.fill("#DateFiledOnBefore", today_str)
            await page.click('input[value="Search"]')
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(3_000)

            html = await page.content()
        finally:
            await page.close()

        if _TOO_MANY_RE.search(html):
            return None

        return _parse_results_page(html)

    async def _fetch_case_detail(self, page: Any, case_id: str) -> dict:
        """Fetch CaseDetail page and return parsed address + court date."""
        url = f"{SEARCH_BASE}/CaseDetail.aspx?CaseID={case_id}"
        try:
            await page.goto(url, timeout=30_000, wait_until="domcontentloaded")
            await page.wait_for_timeout(1_000)
            html = await page.content()
            return _parse_case_detail(html)
        except Exception as e:
            log.warning("Tarrant TX: CaseDetail %s failed: %s", case_id, e)
            return {"property_address": "Unknown", "court_date": None}
