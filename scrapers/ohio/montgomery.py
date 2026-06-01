from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta

import requests
from bs4 import BeautifulSoup

from models.filing import Filing
from scrapers.dates import court_today
from services.name_utils import clean_tenant_name

log = logging.getLogger(__name__)

STATE = "OH"
COUNTY = "Montgomery"
COURT_TIMEZONE = "America/New_York"
NOTICE_TYPE = "Forcible Entry & Detainer"

BASE_URL = "https://clerkofcourt.daytonohio.gov"
SEARCH_URL = f"{BASE_URL}/PA/CvSearchResults.cfm"
CASE_URL_PREFIX = f"{BASE_URL}/PA"

# "Locaton:" is a known typo in the Dayton Municipal Court portal.
# The regex handles "Location:" too in case it is corrected later.
_LOCATON_RE = re.compile(r"Locat(?:ion|on):\s*([^\n]+)", re.IGNORECASE)
_EVICTION_LOC_RE = re.compile(r"Eviction Location:\s*([^\n]+)", re.IGNORECASE | re.MULTILINE)
_COURT_DATE_RE = re.compile(r"Next Court Date:\s*\n?\s*(\d{2}/\d{2}/\d{4})", re.IGNORECASE)

_OCCUPANT_RE = re.compile(
    r"\s+(et\.?\s*al\.?|and\s+all\s+(?:other\s+)?(?:occupants?|tenants?|persons?|others?))$",
    flags=re.IGNORECASE,
)


def _strip_occupant_suffix(name: str) -> str:
    return _OCCUPANT_RE.sub("", name).strip()


def _parse_address(page_text: str) -> str | None:
    """Return the eviction property address from a case detail page's text, or None."""
    m = _LOCATON_RE.search(page_text)
    if m:
        addr = m.group(1).strip()
        if addr:
            return addr
    # Fallback: "Eviction Location:" line in Case History
    m = _EVICTION_LOC_RE.search(page_text)
    if m:
        addr = m.group(1).strip()
        if addr:
            return addr
    return None


def _parse_court_date(page_text: str) -> date | None:
    """Return the next court date from a case detail page's text, or None."""
    m = _COURT_DATE_RE.search(page_text)
    if m:
        try:
            return datetime.strptime(m.group(1), "%m/%d/%Y").date()
        except ValueError:
            pass
    return None


def _parse_results_page(html: str) -> list[dict]:
    """Return a list of CVG (FED eviction) case rows from the search results page.

    Each dict has: case_number, case_url, plaintiff, defendant_raw.
    Non-CVG case types (CVH, CVF, CVI, etc.) are skipped.
    """
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict] = []
    for a in soup.find_all("a", href=lambda h: h and "CvCaseSummary.cfm" in h):
        case_number = a.get_text(strip=True)
        if "-CVG-" not in case_number:
            continue
        row = a.find_parent("tr")
        if not row:
            continue
        cells = row.find_all("td")
        plaintiff = cells[1].get_text(strip=True) if len(cells) > 1 else ""
        defendant_raw = cells[2].get_text(strip=True) if len(cells) > 2 else ""
        rows.append({
            "case_number": case_number,
            "case_url": f"{CASE_URL_PREFIX}/{a['href']}",
            "plaintiff": plaintiff,
            "defendant_raw": defendant_raw,
        })
    return rows


class MontgomeryCountyMunicipalScraper:
    def __init__(self, lookback_days: int = 2):
        self.lookback_days = lookback_days
        self.last_error: str | None = None
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": f"{BASE_URL}/PA/CvSearch-Date.cfm",
        })

    def scrape(self) -> list[Filing]:
        self.last_error = None
        today = court_today(COURT_TIMEZONE)
        filings: list[Filing] = []
        seen_cases: set[str] = set()

        for offset in range(self.lookback_days + 1):
            target = today - timedelta(days=offset)
            date_str = target.strftime("%Y-%m-%d")
            search_url = f"{SEARCH_URL}?runDate={date_str}&DateType=F&type=DATE"

            try:
                html = self._get_text(search_url)
            except Exception as e:
                self.last_error = f"failed to fetch Montgomery results for {date_str}: {e}"
                log.error("Montgomery OH: fetch failed for %s: %s", date_str, e)
                continue

            rows = _parse_results_page(html)
            log.debug("Montgomery OH: %s CVG cases on %s", len(rows), date_str)

            for row in rows:
                case_number = row["case_number"]
                if case_number in seen_cases:
                    continue
                seen_cases.add(case_number)

                try:
                    detail_html = self._get_text(row["case_url"])
                except Exception as e:
                    log.warning(
                        "Montgomery OH: detail fetch failed for %s: %s", case_number, e
                    )
                    detail_html = ""

                detail_text = (
                    BeautifulSoup(detail_html, "html.parser").get_text("\n")
                    if detail_html
                    else ""
                )
                address = _parse_address(detail_text) or "Dayton, OH"
                court_date = _parse_court_date(detail_text)

                tenant_raw = row["defendant_raw"]
                tenant = _strip_occupant_suffix(tenant_raw)

                filings.append(
                    Filing(
                        case_number=case_number,
                        tenant_name=clean_tenant_name(tenant or "") or (tenant_raw or "Unknown"),
                        property_address=address,
                        landlord_name=row["plaintiff"] or "Unknown",
                        filing_date=target,
                        court_date=court_date,
                        state=STATE,
                        county=COUNTY,
                        notice_type=NOTICE_TYPE,
                        source_url=search_url,
                    )
                )

        log.info("Montgomery OH: %s eviction filings found", len(filings))
        return filings

    def _get_text(self, url: str) -> str:
        r = self.session.get(url, timeout=30)
        r.raise_for_status()
        return r.text
