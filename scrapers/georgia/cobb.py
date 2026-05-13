from __future__ import annotations

import io
import logging
import re
import time
from collections import Counter
from datetime import date, datetime, timedelta

import pdfplumber
import requests
from bs4 import BeautifulSoup

from models.filing import Filing
from scrapers.dates import court_today
from scrapers.georgia.cobb_assessor import AddressMatchResult, CobbAssessorClient
from services.nominatim_service import geocode_street_cobb

log = logging.getLogger(__name__)

STATE = "GA"
COUNTY = "Cobb"
COURT_TIMEZONE = "America/New_York"
_CALENDAR_URL = "https://judicial.cobbcounty.gov/mc/magCalendars/"

_COURT_DATE_RE = re.compile(
    r"(MONDAY|TUESDAY|WEDNESDAY|THURSDAY|FRIDAY|SATURDAY|SUNDAY)"
    r",\s+(\w+\s+\d{1,2},\s+\d{4})",
    re.IGNORECASE,
)
_CASE_RE = re.compile(r"^\s*\d+\s+(\d{2}[A-Z]{2,3}\d{4,7})\s+(.*)", re.IGNORECASE)
_VS_RE = re.compile(r"^\s*VS\s*$", re.IGNORECASE)
_HEARING_TYPE_RE = re.compile(
    r"DISPOSSESSORY\s+HEARING|MOTION\s+HEARING|WRIT\s+HEARING", re.IGNORECASE
)
_OCCUPANTS_RE = re.compile(r"AND\s+ALL\s+OCCUPANTS|ET\s+AL\.?", re.IGNORECASE)


class CobbMagistrateCourtScraper:
    """Scrapes Cobb County GA Magistrate Court DISPO PDF calendars for dispossessory cases."""

    def __init__(
        self,
        lookback_days: int = 30,
        max_cases: int = 200,
        enrich_addresses: bool = True,
    ):
        self.lookback_days = lookback_days
        self.max_cases = max_cases
        self.enrich_addresses = enrich_addresses
        self.last_error: str | None = None
        self.address_matches_by_case: dict[str, AddressMatchResult] = {}
        self.address_match_counts: Counter = Counter()

        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; eviction-leadgen/1.0)"})
        self._assessor = CobbAssessorClient(session=self._session)

    def scrape(self) -> list[Filing]:
        self.last_error = None
        self.address_matches_by_case.clear()
        self.address_match_counts.clear()

        today = court_today(COURT_TIMEZONE)
        cutoff = today - timedelta(days=self.lookback_days)

        try:
            html = self._session.get(_CALENDAR_URL, timeout=20).text
        except Exception as e:
            self.last_error = f"Failed to fetch calendar page: {e}"
            log.error("Cobb GA: %s", self.last_error)
            return []

        pdf_links = _dispo_links_from_html(html)
        log.info("Cobb GA: found %d DISPO PDF links on calendar page", len(pdf_links))

        filings: list[Filing] = []
        seen_cases: set[str] = set()
        geocode_cache: dict[str, object] = {}

        for link in pdf_links:
            pdf_date = _parse_date_from_filename(link)
            if pdf_date is None or not (cutoff <= pdf_date <= today + timedelta(days=60)):
                continue

            pdf_url = _CALENDAR_URL + link
            log.info("Cobb GA: fetching PDF %s", link)
            try:
                resp = self._session.get(pdf_url, timeout=30)
                resp.raise_for_status()
                parsed = _parse_pdf_bytes(resp.content)
            except Exception as e:
                log.warning("Cobb GA: PDF parse failed for %s: %s", link, e)
                continue

            court_dt = parsed.get("court_date")
            for case in parsed.get("cases", []):
                if len(filings) >= self.max_cases:
                    break
                case_num = case["case_number"]
                if case_num in seen_cases:
                    continue
                seen_cases.add(case_num)

                landlord = case["plaintiff"] or "Unknown"
                tenant = case["defendant"] or "Unknown"
                property_address = "Unknown"

                if self.enrich_addresses and landlord != "Unknown":
                    match = self._assessor.match_owner(landlord)
                    self.address_matches_by_case[case_num] = match
                    self.address_match_counts[match.status] += 1

                    if match.status == "single_match" and match.records:
                        rec = match.records[0]
                        if rec.situs_addr:
                            geo = geocode_cache.get(rec.situs_addr)
                            if geo is None:
                                time.sleep(1.1)  # Nominatim rate limit
                                geo = geocode_street_cobb(rec.situs_addr)
                                geocode_cache[rec.situs_addr] = geo
                            if geo and geo.postcode:
                                city = geo.city or "Marietta"
                                property_address = (
                                    f"{rec.situs_addr}, {city}, GA {geo.postcode}"
                                )
                else:
                    self.address_match_counts["no_match"] += 1

                filings.append(Filing(
                    case_number=case_num,
                    tenant_name=tenant,
                    property_address=property_address,
                    landlord_name=landlord,
                    filing_date=court_dt or date.today(),
                    court_date=court_dt,
                    state=STATE,
                    county=COUNTY,
                    notice_type="Dispossessory",
                    source_url=pdf_url,
                ))

        log.info("Cobb GA: %d filings found (%d unique)", len(filings), len(seen_cases))
        return filings


def _dispo_links_from_html(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: list[str] = []
    for a in soup.find_all("a", href=True):
        href: str = a["href"]
        if "DISPO" in href.upper():
            links.append(href)
    return links


def _parse_date_from_filename(filename: str) -> date | None:
    """Parse court date from PDF filename: '01 MAY 2026 DISPO 9 AM INMON.pdf'"""
    m = re.match(r"(\d{1,2})\s+([A-Z]{3})\s+(\d{4})", filename, re.IGNORECASE)
    if not m:
        return None
    try:
        return datetime.strptime(f"{m.group(1)} {m.group(2).upper()} {m.group(3)}", "%d %b %Y").date()
    except ValueError:
        return None


def _parse_pdf_bytes(pdf_bytes: bytes) -> dict:
    """Parse a Cobb DISPO PDF. Returns {'court_date': date|None, 'cases': list[dict]}."""
    court_date: date | None = None
    cases: list[dict] = []
    current: dict | None = None
    after_vs = False
    defendant_set = False

    def _finalize() -> None:
        if current and current.get("case_number"):
            cases.append({
                "case_number": current["case_number"],
                "plaintiff": current.get("plaintiff", "").strip(),
                "defendant": current.get("defendant", "").strip(),
            })

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for raw_line in text.splitlines():
                line = raw_line.strip()
                if not line:
                    continue

                # Extract court date from header
                if court_date is None:
                    m = _COURT_DATE_RE.search(line)
                    if m:
                        try:
                            court_date = datetime.strptime(
                                m.group(2).strip(), "%B %d, %Y"
                            ).date()
                        except ValueError:
                            pass
                        continue

                # New case entry
                m = _CASE_RE.match(raw_line)
                if m:
                    _finalize()
                    case_number = m.group(1).upper()
                    plaintiff_raw = re.sub(r"\s{2,}.*$", "", m.group(2)).strip()
                    current = {"case_number": case_number, "plaintiff": plaintiff_raw}
                    after_vs = False
                    defendant_set = False
                    continue

                if current is None:
                    continue

                if _VS_RE.match(line):
                    after_vs = True
                    continue

                if after_vs and _HEARING_TYPE_RE.search(line):
                    continue

                if after_vs and not defendant_set and not _OCCUPANTS_RE.search(line):
                    current["defendant"] = line
                    defendant_set = True

    _finalize()
    return {"court_date": court_date, "cases": cases}
