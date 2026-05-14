from __future__ import annotations

import csv
import html
import io
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from urllib.parse import quote, urljoin

import requests

from models.filing import Filing
from scrapers.dates import court_today

log = logging.getLogger(__name__)

STATE = "OH"
COUNTY = "Franklin"
COURT_TIMEZONE = "America/New_York"

REPORTS_URL = "https://www.fcmcclerk.com/reports/evictions"
BASE_URL = "https://www.fcmcclerk.com"

NOTICE_TYPE = "Civil F.E.D. Eviction"

F_CASE_NUMBER = "CASE_NUMBER"
F_FILE_DATE = "CASE_FILE_DATE"
F_DISPOSITION = "LAST_DISPOSITION_DESCRIPTION"
F_PLAINTIFF_FIRST = "FIRST_PLAINTIFF_FIRST_NAME"
F_PLAINTIFF_MIDDLE = "FIRST_PLAINTIFF_MIDDLE_NAME"
F_PLAINTIFF_LAST = "FIRST_PLAINTIFF_LAST_NAME"
F_PLAINTIFF_SUFFIX = "FIRST_PLAINTIFF_SUFFIX_NAME"
F_PLAINTIFF_COMPANY = "FIRST_PLAINTIFF_COMPANY_NAME"
F_DEF_FIRST = "FIRST_DEFENDANT_FIRST_NAME"
F_DEF_MIDDLE = "FIRST_DEFENDANT_MIDDLE_NAME"
F_DEF_LAST = "FIRST_DEFENDANT_LAST_NAME"
F_DEF_SUFFIX = "FIRST_DEFENDANT_SUFFIX_NAME"
F_DEF_COMPANY = "FIRST_DEFENDANT_COMPANY_NAME"
F_DEF_ADDR1 = "FIRST_DEFENDANT_ADDRESS_LINE_1"
F_DEF_ADDR2 = "FIRST_DEFENDANT_ADDRESS_LINE_2"
F_DEF_CITY = "FIRST_DEFENDANT_CITY"
F_DEF_STATE = "FIRST_DEFENDANT_STATE"
F_DEF_ZIP = "FIRST_DEFENDANT_ZIP"

_REPORT_RE = re.compile(
    r'href="(?P<href>[^"]*FCMC Civil F\.E\.D\. \(Eviction\) Case List '
    r"(?P<start>\d{4}-\d{2}-\d{2}) to (?P<end>\d{4}-\d{2}-\d{2})\.csv\?\d+)",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class FranklinReportLink:
    month_start: date
    url: str


class FranklinCountyMunicipalScraper:
    def __init__(self, lookback_days: int = 2):
        self.lookback_days = lookback_days
        self.last_error: str | None = None
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0"})

    def scrape(self) -> list[Filing]:
        self.last_error = None
        today = court_today(COURT_TIMEZONE)
        cutoff = today - timedelta(days=self.lookback_days)

        try:
            index_html = self._get_text(REPORTS_URL)
        except Exception as e:
            self.last_error = f"failed to fetch FCMC eviction report index: {e}"
            log.error("Franklin OH: failed to fetch report index: %s", e)
            return []

        links = _discover_report_links(
            index_html,
            today=today,
            lookback_days=self.lookback_days,
        )
        if not links:
            self.last_error = "no FCMC eviction report links found"
            return []

        filings: list[Filing] = []
        seen_cases: set[str] = set()

        for link in links:
            try:
                csv_text = self._get_text(link.url)
            except Exception as e:
                log.warning("Franklin OH: failed to fetch %s: %s", link.url, e)
                continue

            for filing in _parse_eviction_csv(csv_text, source_url=link.url):
                if filing.case_number in seen_cases:
                    continue
                if filing.filing_date < cutoff or filing.filing_date > today:
                    continue
                seen_cases.add(filing.case_number)
                filings.append(filing)

        log.info("Franklin OH: %s eviction filings found", len(filings))
        return filings

    def _get_text(self, url: str) -> str:
        r = self.session.get(url, timeout=30)
        r.raise_for_status()
        return r.text


def _discover_report_links(
    html: str,
    *,
    today: date,
    lookback_days: int,
) -> list[FranklinReportLink]:
    cutoff = today - timedelta(days=lookback_days)
    links: list[FranklinReportLink] = []

    for match in _REPORT_RE.finditer(html):
        month_start = _parse_date(match.group("start"), "%Y-%m-%d")
        month_end = _parse_date(match.group("end"), "%Y-%m-%d")
        if month_end < cutoff or month_start > today:
            continue

        href = html_unescape(match.group("href"))
        url = urljoin(BASE_URL, quote(href, safe="/:?=&"))
        links.append(FranklinReportLink(month_start=month_start, url=url))

    links.sort(key=lambda link: link.month_start, reverse=True)
    return links


def _parse_eviction_csv(csv_text: str, *, source_url: str) -> list[Filing]:
    filings: list[Filing] = []
    reader = csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff")))

    for row in reader:
        try:
            case_number = row.get(F_CASE_NUMBER, "").strip()
            if not case_number:
                continue

            filing_date = _parse_date(row.get(F_FILE_DATE, ""), "%m/%d/%Y")
            tenant = _party_name(
                row.get(F_DEF_COMPANY, ""),
                row.get(F_DEF_FIRST, ""),
                row.get(F_DEF_MIDDLE, ""),
                row.get(F_DEF_LAST, ""),
                row.get(F_DEF_SUFFIX, ""),
            )
            landlord = _party_name(
                row.get(F_PLAINTIFF_COMPANY, ""),
                row.get(F_PLAINTIFF_FIRST, ""),
                row.get(F_PLAINTIFF_MIDDLE, ""),
                row.get(F_PLAINTIFF_LAST, ""),
                row.get(F_PLAINTIFF_SUFFIX, ""),
            )
            address = _build_address(
                row.get(F_DEF_ADDR1, ""),
                row.get(F_DEF_ADDR2, ""),
                row.get(F_DEF_CITY, ""),
                row.get(F_DEF_STATE, ""),
                row.get(F_DEF_ZIP, ""),
            )

            filings.append(
                Filing(
                    case_number=case_number,
                    tenant_name=tenant or "Unknown",
                    property_address=address or "Unknown",
                    landlord_name=landlord or "Unknown",
                    filing_date=filing_date,
                    court_date=None,
                    state=STATE,
                    county=COUNTY,
                    notice_type=NOTICE_TYPE,
                    source_url=source_url,
                )
            )
        except Exception as e:
            log.warning("Franklin OH: skipped row %s: %s", row.get(F_CASE_NUMBER, "?"), e)

    return filings


def _party_name(company: str, first: str, middle: str, last: str, suffix: str) -> str:
    company = company.strip()
    if company:
        return company
    return " ".join(part.strip() for part in [first, middle, last, suffix] if part.strip())


def _build_address(line1: str, line2: str, city: str, state: str, zip_: str) -> str:
    line_parts = [part.strip() for part in [line1, line2] if part and part.strip()]
    city = city.strip()
    state = state.strip() or STATE
    zip_ = zip_.strip()

    if city:
        line_parts.append(city)
    if state and zip_:
        line_parts.append(f"{state} {zip_}")
    elif state:
        line_parts.append(state)
    return ", ".join(line_parts)


def _parse_date(raw: str, fmt: str) -> date:
    return datetime.strptime(raw.strip(), fmt).date()


def html_unescape(value: str) -> str:
    return html.unescape(value)
