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
COURT_ID = "7721"
DISCLAIMER_URL = f"{BASE_URL}/disclaimer/{COURT_ID}"
RECORDS_URL = f"{BASE_URL}/records/{COURT_ID}"

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
    """Extract the Laravel CSRF token from a CaseLook HTML page."""
    soup = BeautifulSoup(html, "html.parser")
    token_input = soup.find("input", {"name": "_token"})
    if token_input and token_input.get("value"):
        return token_input["value"]
    meta = soup.find("meta", {"name": "csrf-token"})
    if meta and meta.get("content"):
        return meta["content"]
    return None



def _label_next_text(label_elem) -> str:
    """Return the text node immediately following a <label> element."""
    sib = label_elem.next_sibling
    return str(sib).strip() if sib else ""


def _fetch_case_detail(
    session: requests.Session, record_url: str
) -> tuple[str | None, str | None]:
    """
    GET a CaseLook case detail page and return (defendant_address, plaintiff_name).

    The detail page uses Bootstrap cards with class 'card--parties-MV':
      - One card with <h4>Plaintiff</h4> containing <label>Plaintiff 1:</label>
      - One card with <h4>Defendants</h4> containing <label>Address:</label>
        and <label>City/Sate/ZIP:</label> (note typo in portal)

    Returns (address, landlord) — either may be None if not found.
    """
    try:
        r = session.get(record_url, timeout=15)
        if r.status_code != 200:
            log.warning("Barberton: case detail HTTP %s for %s", r.status_code, record_url)
            return None, None
    except Exception as exc:
        log.warning("Barberton: case detail fetch failed for %s: %s", record_url, exc)
        return None, None

    soup = BeautifulSoup(r.text, "html.parser")
    address: str | None = None
    landlord: str | None = None

    for card in soup.find_all("div", class_="card--parties-MV"):
        header = card.find("h4")
        if not header:
            continue
        header_text = header.get_text(strip=True).lower()
        body = card.find("div", class_="card-body")
        if not body:
            continue

        if "plaintiff" in header_text and not landlord:
            for lbl in body.find_all("label"):
                if re.match(r"Plaintiff\s*1", lbl.get_text(strip=True), re.I):
                    landlord = _label_next_text(lbl) or None
                    break

        elif "defendant" in header_text and not address:
            addr_lbl = None
            city_lbl = None
            for lbl in body.find_all("label"):
                t = lbl.get_text(strip=True)
                if t == "Address:":
                    addr_lbl = lbl
                elif re.match(r"City.*ZIP", t, re.I):
                    city_lbl = lbl
                if addr_lbl and city_lbl:
                    break
            if addr_lbl and city_lbl:
                street = _label_next_text(addr_lbl)
                city_state_zip = _label_next_text(city_lbl)
                if street and city_state_zip:
                    address = f"{street}, {city_state_zip}"
                elif street:
                    address = street

    if not address:
        log.warning("Barberton: no defendant address found at %s", record_url)
    return address, landlord


def _parse_search_results(
    html: str,
    *,
    search_date: date,
) -> list[Filing]:
    """
    Parse a CaseLook search results page into Filing stubs.
    Only CVG (eviction) cases are returned.

    Results use a Bootstrap card layout (not a table):
      - Case number is text in the col-8 h4 of the card header.
      - Record URL is in <a title="Case information">.
      - Tenant name is the text node after <label>Concerning:</label>.
      - Landlord is not in the search results; left as 'Unknown'.
      - property_address is 'Unknown'; upgraded by _fetch_defendant_address().
    """
    soup = BeautifulSoup(html, "html.parser")
    filings: list[Filing] = []

    cards = soup.find_all("div", class_="card--results-case")

    if not cards:
        log.debug("Barberton: no case cards found for %s", search_date)
        return []

    for card in cards:
        # Case number from card header (e.g. "1 CVG2601500")
        header = card.find("div", class_="card-header")
        if not header:
            continue
        m = re.search(r"(CVG\d+)", header.get_text())
        if not m:
            continue
        case_number = m.group(1)

        # Record URL from the "Case information" icon link
        record_link = card.find("a", title="Case information")
        if not record_link:
            continue
        record_url = record_link["href"]

        # Tenant name from the "Concerning:" label
        tenant_raw = ""
        card_body = card.find("div", class_="card-body")
        if card_body:
            concerning = card_body.find("label", string=re.compile(r"Concerning", re.I))
            if concerning and concerning.next_sibling:
                tenant_raw = str(concerning.next_sibling).strip()

        tenant = _strip_occupant_suffix(tenant_raw)

        filings.append(
            Filing(
                case_number=case_number,
                tenant_name=clean_tenant_name(tenant or "") or "Unknown",
                property_address="Unknown",  # upgraded in scrape() via case detail
                landlord_name="Unknown",     # not available in search results
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

    Session flow:
        1. GET /disclaimer/{COURT_ID} → extract <a href="...?accept=TOKEN">Continue</a>
        2. GET /search/{COURT_ID}?accept=TOKEN → session established, CSRF token captured
        3. GET /records/{COURT_ID}?fileDate=...&caseTypes[]=...&perPage=250 → results
        4. GET /record/{COURT_ID}/{id}/{token} per CVG case → defendant address
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
        self._csrf_token: str | None = None

    def _ensure_session(self) -> bool:
        """
        Follow the CaseLook disclaimer flow to establish a valid session.

        Step 1: GET /disclaimer/{COURT_ID} to get the accept link.
        Step 2: GET the accept URL (/search/{COURT_ID}?accept=TOKEN) to land on the
                search page and obtain a session cookie + CSRF token.
        """
        if self._session_ready:
            return True
        try:
            r = self.session.get(DISCLAIMER_URL, timeout=15)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")

            accept_link = soup.find("a", href=re.compile(r"accept="))
            if not accept_link:
                log.error("Barberton: no accept link found on disclaimer page")
                return False

            accept_url = accept_link["href"]
            if accept_url.startswith("/"):
                accept_url = BASE_URL + accept_url

            r = self.session.get(accept_url, timeout=15)
            r.raise_for_status()

            self._csrf_token = _get_csrf_token(r.text)
            self._session_ready = True
            log.debug("Barberton: session established (disclaimer accepted)")
            return True

        except Exception as exc:
            log.error("Barberton: session setup failed: %s", exc)
            return False

    def _get_search(self, search_date: date) -> str:
        """GET /records/{COURT_ID} for the given filing date, returning HTML."""
        date_str = search_date.strftime("%Y-%m-%d")
        params = {
            "_token": self._csrf_token or "",
            "searchType-case": "11",
            "fileDate": date_str,
            "caseTypes[]": '["CVE","CVF","CVG","CVH","CVT"]',
            "perPage": "250",
        }
        r = self.session.get(
            RECORDS_URL,
            params=params,
            headers={"Referer": f"{BASE_URL}/search/{COURT_ID}"},
            timeout=30,
        )
        r.raise_for_status()

        # If the session expired the portal redirects back to the disclaimer
        if "/disclaimer" in r.url:
            self._session_ready = False
            raise RuntimeError("session expired; will re-establish on next run")

        return r.text

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
                html = self._get_search(target)
            except Exception as exc:
                msg = f"Barberton: search failed for {target.isoformat()}: {exc}"
                fetch_errors.append(msg)
                log.error("Barberton: search failed for %s: %s", target.isoformat(), exc)
                continue

            for filing in _parse_search_results(html, search_date=target):
                if filing.case_number in seen_cases:
                    continue
                seen_cases.add(filing.case_number)

                time.sleep(random.uniform(_DELAY_MIN, _DELAY_MAX))

                address, landlord = _fetch_case_detail(self.session, filing.source_url)
                updates: dict = {}
                if address:
                    updates["property_address"] = address
                if landlord:
                    updates["landlord_name"] = landlord
                if updates:
                    filing = filing.model_copy(update=updates)

                filings.append(filing)

        self.last_error = fetch_errors[-1] if fetch_errors and not filings else None

        log.info("Barberton OH: %d eviction filings found", len(filings))
        return filings
