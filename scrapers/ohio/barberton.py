from __future__ import annotations

import logging
import random
import re
import time
from datetime import date, timedelta

import requests
from bs4 import BeautifulSoup

from models.filing import Filing
from scrapers.dates import court_today
from services.name_utils import clean_tenant_name

log = logging.getLogger(__name__)

STATE = "OH"
COUNTY = "Summit"
COURT_TIMEZONE = "America/New_York"
NOTICE_TYPE = "Eviction"

BASE_URL = "https://caselook.barbertonclerkofcourt.com"
DISCLAIMER_URL = f"{BASE_URL}/disclaimer"
SEARCH_URL = f"{BASE_URL}/search"

# Courtesy delay range (seconds) between detail-page fetches.
_DELAY_MIN = 0.5
_DELAY_MAX = 1.5

_OCCUPANT_SUFFIXES = re.compile(
    r"\s+(et\.?\s*al\.?|and\s+all\s+(?:other\s+)?(?:occupants?|tenants?|persons?|others?))$",
    flags=re.IGNORECASE,
)


def _strip_occupant_suffix(name: str) -> str:
    return _OCCUPANT_SUFFIXES.sub("", name).strip()


def _get_csrf_token(html: str) -> str | None:
    """Extract the Laravel CSRF _token from a CaseLook HTML page."""
    soup = BeautifulSoup(html, "html.parser")
    token_input = soup.find("input", {"name": "_token"})
    if token_input and token_input.get("value"):
        return token_input["value"]
    meta = soup.find("meta", {"name": "csrf-token"})
    if meta and meta.get("content"):
        return meta["content"]
    return None


def _parse_address_cell(td) -> str:
    """
    Convert a BeautifulSoup address cell to 'STREET, CITY, STATE ZIP' format.

    CaseLook address cells are typically:
        <td>87 Helen Street<br/>Barberton, OH 44203</td>
    """
    parts = [t.strip() for t in td.stripped_strings]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    street = parts[0]
    city_state_zip = parts[1]
    tokens = city_state_zip.split()
    if len(tokens) >= 3:
        city = " ".join(tokens[:-2])
        state_abbr = tokens[-2]
        zip_code = tokens[-1][:5]  # trim 9-digit ZIPs
        return f"{street}, {city}, {state_abbr} {zip_code}"
    return f"{street}, {city_state_zip}"


def _fetch_defendant_address(session: requests.Session, record_url: str) -> str | None:
    """
    GET a CaseLook case detail page and return the first defendant's address,
    or None if the address cannot be found.
    """
    try:
        r = session.get(record_url, timeout=15)
        if r.status_code != 200:
            log.warning("Barberton: case detail HTTP %s for %s", r.status_code, record_url)
            return None
    except Exception as exc:
        log.warning("Barberton: case detail fetch failed for %s: %s", record_url, exc)
        return None

    soup = BeautifulSoup(r.text, "html.parser")

    # Strategy 1: table-based party section (most common CaseLook layout)
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if not cells:
                continue
            first_cell_text = cells[0].get_text(strip=True).upper()
            if "DEFENDANT" in first_cell_text:
                if len(cells) >= 3:
                    addr = _parse_address_cell(cells[2])
                    if addr:
                        return addr
                if len(cells) == 2:
                    addr = _parse_address_cell(cells[1])
                    if addr:
                        return addr

    # Strategy 2: Bootstrap row/col layout
    for elem in soup.find_all(string=re.compile(r"^Defendant$", re.IGNORECASE)):
        parent = elem.find_parent()
        if not parent:
            continue
        container = parent.find_parent()
        if container:
            for sibling in container.find_next_siblings():
                text = sibling.get_text(" ", strip=True)
                if re.search(r"\d+\s+\w+.*OH\s+\d{5}", text, re.IGNORECASE):
                    return text.strip()

    # Strategy 3: Regex over full page text (last resort)
    text = soup.get_text("\n")
    defendant_idx = text.upper().find("DEFENDANT")
    if defendant_idx >= 0:
        snippet = text[defendant_idx: defendant_idx + 400]
        match = re.search(
            r"(\d+\s+[\w\s.]+(?:St(?:reet)?|Ave(?:nue)?|Rd|Road|Dr(?:ive)?|Blvd|Ln|Lane|"
            r"Ct|Court|Way|Pl(?:ace)?|Cir(?:cle)?|Hwy|Highway)[,\s]+[\w\s]+,?\s*OH\s+\d{5})",
            snippet,
            re.IGNORECASE,
        )
        if match:
            return match.group(1).strip()

    log.warning("Barberton: no defendant address found at %s", record_url)
    return None


def _parse_search_results(
    html: str,
    *,
    search_date: date,
) -> list[Filing]:
    """
    Parse a CaseLook search results page into Filing stubs.
    Only CVG (eviction) cases are returned.
    property_address is set to 'Unknown'; upgraded by _fetch_defendant_address().
    """
    soup = BeautifulSoup(html, "html.parser")
    filings: list[Filing] = []

    record_links = soup.find_all("a", href=re.compile(r"^/record/"))

    if not record_links:
        log.debug("Barberton: no /record/ links found for %s", search_date)
        return []

    for link in record_links:
        case_number = link.get_text(strip=True)
        if not case_number.startswith("CVG"):
            continue

        record_url = BASE_URL + link["href"]

        landlord = ""
        tenant_raw = ""
        tr = link.find_parent("tr")
        if tr:
            cells = tr.find_all("td")
            if len(cells) >= 3:
                landlord = cells[1].get_text(strip=True)
                tenant_raw = cells[2].get_text(strip=True)
            elif len(cells) == 2:
                landlord = cells[1].get_text(strip=True)

        tenant = _strip_occupant_suffix(tenant_raw)

        filings.append(
            Filing(
                case_number=case_number,
                tenant_name=clean_tenant_name(tenant or "") or "Unknown",
                property_address="Unknown",  # upgraded in scrape() via case detail
                landlord_name=landlord or "Unknown",
                filing_date=search_date,
                court_date=None,
                state=STATE,
                county=COUNTY,
                notice_type=NOTICE_TYPE,
                source_url=record_url,
            )
        )

    log.debug("Barberton: parsed %d CVG filings from results for %s", len(filings), search_date)
    return filings


class BarbertonMunicipalScraper:
    """
    Scraper for Barberton Municipal Court (Summit County OH) eviction filings.

    Portal: https://caselook.barbertonclerkofcourt.com (CaseLook / Henschen & Associates)

    Two-step flow:
        1. POST /search with filing date → parse CVG case links from results table
        2. GET /record/{id}/{token} per case → extract defendant address
    """

    def __init__(self, lookback_days: int = 2):
        self.lookback_days = lookback_days
        self.last_error: str | None = None
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": BASE_URL,
        })
        self._session_ready = False

    def scrape(self) -> list[Filing]:
        today = court_today(COURT_TIMEZONE)

        if not self._ensure_session():
            self.last_error = "Barberton: could not establish session (disclaimer step failed)"
            log.error(self.last_error)
            return []

        filings: list[Filing] = []
        seen_cases: set[str] = set()
        fetch_errors: list[str] = []

        for offset in range(self.lookback_days + 1):
            target = today - timedelta(days=offset)
            try:
                html = self._post_search(target)
            except Exception as exc:
                msg = f"Barberton: search failed for {target.isoformat()}: {exc}"
                fetch_errors.append(msg)
                log.error("Barberton: search failed for %s: %s", target.isoformat(), exc)
                continue

            for filing in _parse_search_results(
                html, search_date=target
            ):
                if filing.case_number in seen_cases:
                    continue
                seen_cases.add(filing.case_number)

                time.sleep(random.uniform(_DELAY_MIN, _DELAY_MAX))

                address = _fetch_defendant_address(self.session, filing.source_url)
                if address:
                    filing = filing.model_copy(update={"property_address": address})

                filings.append(filing)

        # Only surface an error when every day failed and no filings were returned.
        self.last_error = fetch_errors[-1] if fetch_errors and not filings else None

        log.info("Barberton OH: %d eviction filings found", len(filings))
        return filings

    def _ensure_session(self) -> bool:
        """Accept the CaseLook disclaimer to obtain a valid session cookie."""
        if self._session_ready:
            return True
        try:
            r = self.session.get(DISCLAIMER_URL, timeout=15)
            r.raise_for_status()
            token = _get_csrf_token(r.text)

            if not token:
                r = self.session.get(f"{BASE_URL}/", timeout=15)
                r.raise_for_status()
                token = _get_csrf_token(r.text)

            if not token:
                log.error("Barberton: could not extract CSRF token from disclaimer page")
                return False

            r = self.session.post(
                DISCLAIMER_URL,
                data={"_token": token, "accept": "1"},
                headers={"Referer": BASE_URL},
                timeout=15,
                allow_redirects=True,
            )
            r.raise_for_status()
            self._session_ready = True
            log.debug("Barberton: session established (disclaimer accepted)")
            return True

        except Exception as exc:
            log.error("Barberton: session setup failed: %s", exc)
            return False

    def _post_search(self, search_date: date) -> str:
        """Fetch a fresh CSRF token then POST the search form for the given date."""
        r = self.session.get(f"{BASE_URL}/", timeout=15)
        r.raise_for_status()
        token = _get_csrf_token(r.text)
        if not token:
            r = self.session.get(SEARCH_URL, timeout=15)
            r.raise_for_status()
            token = _get_csrf_token(r.text)
        if not token:
            raise RuntimeError("could not extract CSRF token for search POST")

        date_str = search_date.strftime("%m/%d/%Y")
        r = self.session.post(
            SEARCH_URL,
            data={
                "_token": token,
                "searchDate": date_str,
                "dateType": "fileDate",
                "caseTypes[]": "CVG",
            },
            headers={"Referer": f"{BASE_URL}/"},
            timeout=30,
        )
        r.raise_for_status()
        return r.text

