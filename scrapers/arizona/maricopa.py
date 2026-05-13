from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import urljoin

import requests

from models.filing import Filing
from scrapers.dates import court_today
from scrapers.arizona.maricopa_assessor import (
    AddressMatchResult,
    MaricopaAssessorClient,
)

log = logging.getLogger(__name__)

STATE = "AZ"
COUNTY = "Maricopa"
COURT_TIMEZONE = "America/Phoenix"

BASE_URL = "https://justicecourts.maricopa.gov/app/courtrecords/"
CALENDAR_INDEX_URL = urljoin(BASE_URL, "CourtCalendars")


@dataclass(frozen=True)
class MaricopaCalendarCase:
    case_number: str
    court_name: str
    court_date: date
    court_time: str
    notice_type: str
    landlord_name: str
    tenant_name: str
    detail_path: str
    calendar_url: str


@dataclass(frozen=True)
class MaricopaCaseDetail:
    filing_date: date
    status: str
    address_match: AddressMatchResult | None = None


class MaricopaJusticeCourtScraper:
    """
    Scraper-only proof for Maricopa County Justice Court eviction calendars.

    The public calendar enumerates upcoming eviction hearings and detail pages
    expose file dates. Tested pages do not expose property/defendant addresses,
    so generated filings deliberately use ``Unknown`` for property_address.
    """

    def __init__(
        self,
        lookback_days: int = 7,
        max_cases: int | None = None,
        enrich_addresses: bool = False,
        assessor_client: MaricopaAssessorClient | None = None,
    ):
        self.lookback_days = lookback_days
        self.max_cases = max_cases
        self.enrich_addresses = enrich_addresses
        self.assessor_client = assessor_client or MaricopaAssessorClient()
        self.address_match_counts: dict[str, int] = {
            "single_match": 0,
            "ambiguous": 0,
            "no_match": 0,
            "error": 0,
        }
        self.address_matches_by_case: dict[str, AddressMatchResult] = {}
        self.last_error: str | None = None
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0"})

    def scrape(self) -> list[Filing]:
        self.last_error = None
        try:
            court_links = self._fetch_court_links()
        except Exception as e:
            self.last_error = f"failed to fetch calendar index: {e}"
            log.error("Maricopa AZ: failed to fetch calendar index: %s", e)
            return []

        filings: list[Filing] = []
        seen_cases: set[str] = set()

        for court_name, calendar_url in court_links:
            try:
                calendar_html = self._get(calendar_url)
                cases = _parse_calendar_html(calendar_html, court_name, calendar_url)
            except Exception as e:
                log.warning("Maricopa AZ: failed calendar %s: %s", court_name, e)
                continue

            for case in cases:
                if case.case_number in seen_cases:
                    continue
                seen_cases.add(case.case_number)

                detail_url = urljoin(BASE_URL, case.detail_path)
                try:
                    detail = _parse_case_detail_html(self._get(detail_url))
                    if self.enrich_addresses:
                        match = self.assessor_client.match_owner(case.landlord_name)
                        self.address_match_counts[match.status] += 1
                        self.address_matches_by_case[case.case_number] = match
                        detail = MaricopaCaseDetail(
                            filing_date=detail.filing_date,
                            status=detail.status,
                            address_match=match,
                        )
                except Exception as e:
                    log.warning("Maricopa AZ: failed detail %s: %s", case.case_number, e)
                    continue

                filings.append(self._build_filing(case, detail, detail_url))
                if self.max_cases is not None and len(filings) >= self.max_cases:
                    log.info("Maricopa AZ: stopping at max_cases=%s", self.max_cases)
                    return filings

        log.info("Maricopa AZ: %s eviction calendar filings found", len(filings))
        return filings

    def _fetch_court_links(self) -> list[tuple[str, str]]:
        index_html = self._get(CALENDAR_INDEX_URL)
        links = _parse_court_links(index_html)
        if not links:
            raise RuntimeError("no court calendar links found")
        return links

    def _get(self, url: str) -> str:
        r = self.session.get(url, timeout=30)
        r.raise_for_status()
        return r.text

    def _build_filing(
        self,
        case: MaricopaCalendarCase,
        detail: MaricopaCaseDetail,
        detail_url: str,
    ) -> Filing:
        return Filing(
            case_number=case.case_number,
            tenant_name=case.tenant_name or "Unknown",
            property_address=self._property_address(detail),
            landlord_name=case.landlord_name or "Unknown",
            filing_date=detail.filing_date,
            court_date=case.court_date,
            state=STATE,
            county=COUNTY,
            notice_type=case.notice_type,
            source_url=detail_url,
        )

    @staticmethod
    def _property_address(detail: MaricopaCaseDetail) -> str:
        match = detail.address_match
        if match and match.status == "single_match" and match.records:
            return match.records[0].physical_address or "Unknown"
        return "Unknown"


def _parse_court_links(index_html: str) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    for href, label in re.findall(
        r'<a\s+href="(?P<href>CourtCalendar\?[^"]+)">(?P<label>[^<]+)</a>',
        index_html,
        flags=re.IGNORECASE,
    ):
        links.append((_clean_text(label), urljoin(BASE_URL, html.unescape(href))))
    return links


def _parse_calendar_html(
    calendar_html: str,
    court_name: str,
    calendar_url: str,
) -> list[MaricopaCalendarCase]:
    cases: list[MaricopaCalendarCase] = []
    blocks = _calendar_event_blocks(calendar_html)

    for index, block in enumerate(blocks):
        title = _extract_class_text(block, "jc-cc-case-title")
        if "eviction action hearing" not in title.lower():
            continue

        detail_path, case_number = _extract_case_link(block)
        if not case_number:
            continue

        tenant = ""
        if index + 1 < len(blocks):
            tenant = _extract_class_text(blocks[index + 1], "jc-cc-case-party2")

        cases.append(
            MaricopaCalendarCase(
                case_number=case_number,
                court_name=court_name,
                court_date=_parse_date(_extract_class_text(block, "jc-cc-case-date")),
                court_time=_extract_class_text(block, "jc-cc-case-time"),
                notice_type=title,
                landlord_name=_extract_class_text(block, "jc-cc-case-party"),
                tenant_name=tenant or "Unknown",
                detail_path=detail_path,
                calendar_url=calendar_url,
            )
        )

    return cases


def _parse_case_detail_html(detail_html: str) -> MaricopaCaseDetail:
    text = _clean_text(detail_html)
    filing_match = re.search(r"File Date:\s*(\d{1,2}/\d{1,2}/\d{4})", text)
    if not filing_match:
        raise ValueError("file date not found")

    status = ""
    status_match = re.search(
        r"Case Status:\s*(.+?)(?:\s+Party Information|\s+Disposition Information|\s+Case Documents)",
        text,
        flags=re.IGNORECASE,
    )
    if status_match:
        status = status_match.group(1).strip()

    return MaricopaCaseDetail(
        filing_date=_parse_date(filing_match.group(1)),
        status=status,
    )


def _calendar_event_blocks(calendar_html: str) -> list[str]:
    starts = [
        match.start()
        for match in re.finditer(
            r'<div[^>]+id="MainContent_CourtCalendarRepeater_DivCaseCalendarWrapper_\d+"',
            calendar_html,
            flags=re.IGNORECASE,
        )
    ]
    blocks: list[str] = []
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(calendar_html)
        blocks.append(calendar_html[start:end])
    return blocks


def _extract_case_link(block: str) -> tuple[str, str]:
    match = re.search(
        r'<a[^>]+href="(?P<href>CaseInfo\.aspx\?casenumber=[^"]+)"[^>]*>(?P<text>.*?)</a>',
        block,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return "", ""
    return html.unescape(match.group("href")), _clean_text(match.group("text"))


def _extract_class_text(block: str, class_name: str) -> str:
    match = re.search(
        rf'<div[^>]+class="[^"]*(?<![\w-]){re.escape(class_name)}(?![\w-])[^"]*"[^>]*>(?P<body>.*?)</div>',
        block,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return ""
    return _clean_text(match.group("body"))


def _clean_text(raw: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", raw, flags=re.IGNORECASE)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_date(raw: str) -> date:
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {raw!r}")
