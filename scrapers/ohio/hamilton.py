from __future__ import annotations

import logging
import re
from datetime import date, timedelta

import requests
from bs4 import BeautifulSoup

from models.filing import Filing
from scrapers.dates import court_today

log = logging.getLogger(__name__)

STATE = "OH"
COUNTY = "Hamilton"
COURT_TIMEZONE = "America/New_York"
NOTICE_TYPE = "Eviction"

BASE_URL = "https://www.courtclerk.org/data/eviction_schedule.php"
COURT = "MCV"
LOCATION = "EVIM"

_OCCUPANT_SUFFIXES = re.compile(
    r"\s+(et\.?\s*al\.?|and\s+all\s+(?:other\s+)?(?:occupants?|tenants?|persons?|others?))$",
    flags=re.IGNORECASE,
)


class HamiltonCountyMunicipalScraper:
    def __init__(self, lookback_days: int = 2):
        self.lookback_days = lookback_days
        self.last_error: str | None = None
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Referer": "https://www.courtclerk.org/records-search/eviction-schedule-search/",
        })

    def scrape(self) -> list[Filing]:
        self.last_error = None
        today = court_today(COURT_TIMEZONE)

        filings: list[Filing] = []
        seen_cases: set[str] = set()

        for offset in range(self.lookback_days + 1):
            target = today - timedelta(days=offset)
            date_str = f"{target.month}/{target.day}/{target.year}"
            url = f"{BASE_URL}?chosendate={date_str}&court={COURT}&location={LOCATION}"

            try:
                html = self._get_text(url)
            except Exception as e:
                self.last_error = f"failed to fetch Hamilton eviction schedule for {date_str}: {e}"
                log.error("Hamilton OH: fetch failed for %s: %s", date_str, e)
                continue

            for filing in _parse_eviction_schedule(html, hearing_date=target, source_url=url):
                if filing.case_number in seen_cases:
                    continue
                seen_cases.add(filing.case_number)
                filings.append(filing)

        log.info("Hamilton OH: %s eviction filings found", len(filings))
        return filings

    def _get_text(self, url: str) -> str:
        r = self.session.get(url, timeout=30)
        r.raise_for_status()
        return r.text


def _parse_eviction_schedule(
    html: str,
    *,
    hearing_date: date,
    source_url: str = BASE_URL,
) -> list[Filing]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", {"id": "judge_schedule_table"})
    if not table:
        return []

    rows = table.find_all("tr")
    filings: list[Filing] = []
    i = 0

    while i < len(rows):
        tds = rows[i].find_all("td")
        if not tds:
            i += 1
            continue

        first_td = tds[0]
        bg = first_td.get("style", "")
        if "#174c8c" not in bg:
            i += 1
            continue

        if len(tds) < 2 or i + 2 >= len(rows):
            i += 1
            continue

        case_td = tds[1]
        case_number = case_td.get_text(" ", strip=True).split()[0] if case_td else ""

        plaintiff_row = rows[i + 1].find_all("td") if i + 1 < len(rows) else []
        defendant_row = rows[i + 2].find_all("td") if i + 2 < len(rows) else []

        landlord = plaintiff_row[1].get_text(strip=True) if len(plaintiff_row) > 1 else ""
        tenant_raw = defendant_row[1].get_text(strip=True) if len(defendant_row) > 1 else ""
        tenant = _strip_occupant_suffix(tenant_raw)

        if not case_number:
            i += 1
            continue

        filings.append(
            Filing(
                case_number=case_number,
                tenant_name=tenant or "Unknown",
                property_address="Unknown",
                landlord_name=landlord or "Unknown",
                filing_date=hearing_date,
                court_date=hearing_date,
                state=STATE,
                county=COUNTY,
                notice_type=NOTICE_TYPE,
                source_url=source_url,
            )
        )
        i += 4

    return filings


def _strip_occupant_suffix(name: str) -> str:
    return _OCCUPANT_SUFFIXES.sub("", name).strip()
