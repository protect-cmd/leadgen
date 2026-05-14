from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Any

from bs4 import BeautifulSoup

from models.filing import Filing
from scrapers.dates import court_today

log = logging.getLogger(__name__)

STATE = "NV"
COUNTY = "Clark"
COURT_TIMEZONE = "America/Los_Angeles"

BASE_URL = "https://cvpublicaccess.clarkcountynv.gov/eservices/calendar.page"

_OCCUPANT_SUFFIXES = re.compile(
    r"\s+(et\.?\s*al\.?|and\s+all\s+other\s+(occupants?|tenants?|persons?|others?)|and\s+all\s+others?)$",
    flags=re.IGNORECASE,
)

_VS_SPLIT = re.compile(r"\s*\bvs\b\s+", flags=re.IGNORECASE)

_TRAILING_STATUS = re.compile(
    r"\s*\b(OPEN|CLOSED|INACTIVE|DISPOSED|HELD|CONTINUED|REOPEN|VACATED)\b[^,]*$",
    flags=re.IGNORECASE,
)


def _strip_occupant_suffix(name: str) -> str:
    return _OCCUPANT_SUFFIXES.sub("", name).strip()


def _parse_case_description(desc: str) -> tuple[str, str, str] | None:
    """Return (case_number, landlord, tenant) or None if no VS separator found."""
    parts = desc.split(None, 1)
    if not parts:
        return None

    case_number = parts[0]
    remainder = parts[1].strip() if len(parts) > 1 else ""

    vs_parts = _VS_SPLIT.split(remainder, maxsplit=1)
    if len(vs_parts) != 2:
        return None

    landlord = vs_parts[0].strip() or "Unknown"

    tenant_raw = _TRAILING_STATUS.sub("", vs_parts[1]).strip()
    tenant = _strip_occupant_suffix(tenant_raw) or "Unknown"

    return case_number, landlord, tenant


def _parse_listable_events(
    html: str,
    *,
    hearing_date: date,
    source_url: str = BASE_URL,
) -> list[Filing]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="listTable")
    if not table:
        return []

    filings: list[Filing] = []
    for row in table.find_all("tr"):
        tds = row.find_all("td")
        if len(tds) < 5:
            continue

        event_type = tds[1].get_text(strip=True)
        if "EVIC" not in event_type.upper():
            continue

        case_desc = tds[4].get_text(" ", strip=True)
        parsed = _parse_case_description(case_desc)
        if parsed is None:
            continue
        case_number, landlord, tenant = parsed
        if not case_number:
            continue

        filings.append(
            Filing(
                case_number=case_number,
                tenant_name=tenant,
                property_address="Unknown",
                landlord_name=landlord,
                filing_date=hearing_date,
                court_date=hearing_date,
                state=STATE,
                county=COUNTY,
                notice_type=f"Eviction / {event_type}",
                source_url=source_url,
            )
        )

    return filings


def _format_date_label(target: date) -> str:
    day_name = target.strftime("%A")
    month_name = target.strftime("%B")
    return f"{day_name}, {month_name} {target.day} {target.year}"


class ClarkCountyJusticeCourtScraper:
    def __init__(self, lookback_days: int = 2, max_cases: int | None = None):
        self.lookback_days = lookback_days
        self.max_cases = max_cases
        self.last_error: str | None = None

    async def scrape(self) -> list[Filing]:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.last_error = "playwright not installed"
            log.error("Clark NV: playwright not installed")
            return []

        self.last_error = None
        today = court_today(COURT_TIMEZONE)
        filings: list[Filing] = []
        seen_cases: set[str] = set()

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                )
                page = await context.new_page()

                for offset in range(self.lookback_days + 1):
                    target = today - timedelta(days=offset)
                    target_label = _format_date_label(target)

                    await page.goto(BASE_URL, wait_until="networkidle", timeout=30_000)

                    date_link_id = await self._find_date_link(page, target_label)
                    if not date_link_id:
                        log.warning("Clark NV: no calendar cell for %s", target_label)
                        continue

                    await page.evaluate(f'document.getElementById("{date_link_id}").click()')
                    await page.wait_for_timeout(3_000)

                    day_done = False
                    for _pg in range(30):
                        html = await page.content()
                        for filing in _parse_listable_events(html, hearing_date=target, source_url=BASE_URL):
                            if filing.case_number in seen_cases:
                                continue
                            seen_cases.add(filing.case_number)
                            filings.append(filing)
                            if self.max_cases and len(filings) >= self.max_cases:
                                day_done = True
                                break

                        if day_done:
                            break

                        next_url = self._next_page_url(html)
                        if not next_url:
                            break
                        await page.goto(next_url, wait_until="networkidle", timeout=30_000)

                    if day_done:
                        break

            except Exception as e:
                self.last_error = str(e)
                log.error("Clark NV: scrape error: %s", e, exc_info=True)
            finally:
                await browser.close()

        log.info("Clark NV: %s eviction filings found", len(filings))
        return filings

    async def _find_date_link(self, page: Any, target_label: str) -> str | None:
        content = await page.content()
        soup = BeautifulSoup(content, "html.parser")
        for span in soup.find_all("span", class_="visually-hidden"):
            if target_label in span.get_text(strip=True):
                td = span.find_parent("td")
                if td:
                    a = td.find("a")
                    if a:
                        return a.get("id")
        return None

    @staticmethod
    def _next_page_url(html: str) -> str | None:
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a"):
            if a.get_text(strip=True) == ">" and a.get("href", "").startswith("?x="):
                return BASE_URL + a["href"]
        return None
