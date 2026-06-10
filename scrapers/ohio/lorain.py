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
COUNTY = "Lorain"
COURT_TIMEZONE = "America/New_York"
NOTICE_TYPE = "Eviction"

BASE_URL = "https://eservices.elyriamunicourt.org"
ENTRY_URL = f"{BASE_URL}/eservices/casesearch"
SEARCH_RESULTS_URL = f"{BASE_URL}/eservices/searchresults.page"

# Courtesy delay range (seconds) between detail-page fetches.
_DELAY_MIN = 0.5
_DELAY_MAX = 1.5

_OCCUPANT_SUFFIXES = re.compile(
    r"\s+(et\.?\s*al\.?|and\s+all\s+(?:other\s+)?(?:occupants?|tenants?|persons?|others?))$",
    flags=re.IGNORECASE,
)


def _strip_occupant_suffix(name: str) -> str:
    return _OCCUPANT_SUFFIXES.sub("", name).strip()


def _parse_hidden_field(html: str) -> tuple[str, str] | None:
    """
    Extract the Wicket hidden field name and value from the search form.

    CourtView uses a Wicket framework field like:
        <input type="hidden" name="id5_hf_0" value="" />
    The field name varies per session. Returns (name, value) or None.
    """
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form")
    if not form:
        return None
    hidden = form.find("input", {"type": "hidden"})
    if hidden and hidden.get("name"):
        return (hidden["name"], hidden.get("value", ""))
    return None


def _parse_form_action(html: str) -> str | None:
    """Extract the form action URL from the search form HTML."""
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form")
    if form and form.get("action"):
        action = form["action"]
        if action.startswith("http"):
            return action
        return BASE_URL + action
    return None


def _parse_case_rows(html: str) -> list[dict]:
    """
    Parse rows from the CourtView search results table.

    Each row maps to one party per case. Cell layout:
        cells[2]: case number + detail link (<a href="searchresults.page?x=...">)
        cells[3]: case type (e.g. "Eviction (CVG)")
        cells[4]: file date (MM/DD/YYYY)
        cells[6]: party name
        cells[7]: party type ("Plaintiff" or "Defendant")

    Returns a list of dicts with keys: case_number, detail_href, case_type,
    file_date_str, party_name, party_type.
    """
    soup = BeautifulSoup(html, "html.parser")
    rows = []

    # Find the results table — it has alternating data rows (no thead class needed)
    for tr in soup.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 8:
            continue

        # cells[2] must contain a link for it to be a case row
        link = cells[2].find("a", href=True)
        if not link:
            continue

        case_number = cells[2].get_text(strip=True)
        if not case_number:
            continue

        href = link["href"]
        # Make absolute if relative
        if not href.startswith("http"):
            href = BASE_URL + "/eservices/" + href.lstrip("/")

        rows.append({
            "case_number": case_number,
            "detail_href": href,
            "case_type": cells[3].get_text(strip=True),
            "file_date_str": cells[4].get_text(strip=True),
            "party_name": cells[6].get_text(strip=True),
            "party_type": cells[7].get_text(strip=True),
        })

    return rows


def _group_by_case(rows: list[dict]) -> dict[str, dict]:
    """
    Group party rows by case number.

    For each case, keep:
    - The first Defendant row as tenant
    - The first Plaintiff row as landlord
    - The case's detail_href and file_date_str (from any row)
    """
    cases: dict[str, dict] = {}
    for row in rows:
        cn = row["case_number"]
        if cn not in cases:
            cases[cn] = {
                "case_number": cn,
                "detail_href": row["detail_href"],
                "file_date_str": row["file_date_str"],
                "tenant_raw": "",
                "landlord": "",
            }
        ptype = row["party_type"].strip().upper()
        if ptype == "DEFENDANT" and not cases[cn]["tenant_raw"]:
            cases[cn]["tenant_raw"] = row["party_name"]
        elif ptype == "PLAINTIFF" and not cases[cn]["landlord"]:
            cases[cn]["landlord"] = row["party_name"]
    return cases


def _parse_filing_date(date_str: str) -> date | None:
    """Parse MM/DD/YYYY into a date object, returning None on failure."""
    try:
        return date(*map(int, [date_str[6:10], date_str[0:2], date_str[3:5]]))
    except Exception:
        return None


def _fetch_defendant_address(session: requests.Session, detail_url: str) -> str | None:
    """
    GET a CourtView case detail page and return the first defendant's address.

    The detail page body text has a pattern like:

        PEREZ, ANGEL
        - Defendant
        Disposition
        Disp Date
        Address
        138 EDGEWOOD ST
        ELYRIA ,   OH   44035

    We find "- Defendant" sections and then grab the two lines after "Address".
    Returns 'STREET, CITY, STATE ZIP' or None.
    """
    try:
        r = session.get(detail_url, timeout=15)
        if r.status_code != 200:
            log.warning("Lorain: case detail HTTP %s for %s", r.status_code, detail_url)
            return None
    except Exception as exc:
        log.warning("Lorain: case detail fetch failed for %s: %s", detail_url, exc)
        return None

    # Split body text into lines for structured parsing
    soup = BeautifulSoup(r.text, "html.parser")
    text = soup.get_text("\n")
    lines = [ln.strip() for ln in text.splitlines()]

    # Find the first "- Defendant" section, then look for "Address" followed by
    # two non-empty lines (street, city/state/zip).
    in_defendant = False
    for i, line in enumerate(lines):
        if line == "- Defendant":
            in_defendant = True
        if in_defendant and line.upper() == "ADDRESS":
            # Collect the next two non-empty lines
            addr_lines = []
            for j in range(i + 1, min(i + 10, len(lines))):
                if lines[j]:
                    addr_lines.append(lines[j])
                if len(addr_lines) == 2:
                    break
            if len(addr_lines) == 2:
                street = addr_lines[0]
                # Normalize extra whitespace in city/state/zip line
                # e.g. "ELYRIA ,   OH   44035" → "ELYRIA , OH 44035"
                city_line = re.sub(r"\s+", " ", addr_lines[1])
                m = re.match(r"^(.+?)\s*,\s*([A-Z]{2})\s+(\d{5})", city_line)
                if m:
                    city, state_abbr, zip_code = m.group(1).strip(), m.group(2), m.group(3)
                    return f"{street}, {city}, {state_abbr} {zip_code}"
                # Fallback: return raw two lines joined
                return f"{street}, {city_line}"
            # Found "Address" label but not enough lines — stop looking
            break

    log.warning("Lorain: no defendant address found at %s", detail_url)
    return None


class ElyriaMunicipalScraper:
    """
    Scraper for Elyria Municipal Court (Lorain County OH) eviction filings.

    Portal: https://eservices.elyriamunicourt.org/eservices/ (CourtView / equivant)

    Two-step flow:
        1. GET /eservices/casesearch → establish session, parse form action + Wicket hidden field
           POST form with date range + caseCd=CVG → redirects to searchresults.page
        2. GET searchresults.page?x=<token> per case → extract first defendant address
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
        })

    def scrape(self) -> list[Filing]:
        today = court_today(COURT_TIMEZONE)
        begin = today - timedelta(days=self.lookback_days)
        end = today

        filings: list[Filing] = []
        seen_cases: set[str] = set()
        fetch_errors: list[str] = []

        try:
            results_html = self._search(begin, end)
        except Exception as exc:
            msg = f"Lorain: search failed for {begin.isoformat()}–{end.isoformat()}: {exc}"
            fetch_errors.append(msg)
            log.error("Lorain: search failed for %s–%s: %s", begin.isoformat(), end.isoformat(), exc)
            self.last_error = msg
            return []

        rows = _parse_case_rows(results_html)
        cases = _group_by_case(rows)

        log.debug("Lorain: %d unique cases from results page", len(cases))

        for cn, case in cases.items():
            if cn in seen_cases:
                continue
            seen_cases.add(cn)

            filing_date = _parse_filing_date(case["file_date_str"]) or today
            tenant = _strip_occupant_suffix(case["tenant_raw"])

            filing = Filing(
                case_number=cn,
                tenant_name=clean_tenant_name(tenant or "") or "Unknown",
                property_address="Unknown",  # upgraded below via case detail
                landlord_name=case["landlord"] or "Unknown",
                filing_date=filing_date,
                court_date=None,
                state=STATE,
                county=COUNTY,
                notice_type=NOTICE_TYPE,
                source_url=case["detail_href"],
            )

            time.sleep(random.uniform(_DELAY_MIN, _DELAY_MAX))

            try:
                address = _fetch_defendant_address(self.session, case["detail_href"])
            except Exception as exc:
                msg = f"Lorain: detail fetch failed for {cn}: {exc}"
                fetch_errors.append(msg)
                log.warning("Lorain: detail fetch failed for %s: %s", cn, exc)
                address = None

            if address:
                filing = filing.model_copy(update={"property_address": address})

            filings.append(filing)

        # Only surface an error when no filings were returned.
        self.last_error = fetch_errors[-1] if fetch_errors and not filings else None

        log.info("Lorain OH: %d eviction filings found", len(filings))
        return filings

    def _search(self, begin: date, end: date) -> str:
        """
        Load the CourtView search form, then POST the CVG date-range query.
        Returns the HTML of the search results page.
        """
        # Step 1: GET entry URL — establishes session cookie, returns form HTML
        r = self.session.get(ENTRY_URL, timeout=15, allow_redirects=True)
        r.raise_for_status()

        form_html = r.text
        form_action = _parse_form_action(form_html)
        hidden = _parse_hidden_field(form_html)

        if not form_action:
            raise RuntimeError("Lorain: could not find form action in search page")
        if not hidden:
            raise RuntimeError("Lorain: could not find Wicket hidden field in search form")

        hidden_name, hidden_value = hidden

        # Step 2: POST the search form
        begin_str = begin.strftime("%m/%d/%Y")
        end_str = end.strftime("%m/%d/%Y")

        post_data = {
            hidden_name: hidden_value,
            "fileDateRange:dateInputBegin": begin_str,
            "fileDateRange:dateInputEnd": end_str,
            "caseCd": "CVG",
            "submitLink": "Search",
        }

        r = self.session.post(
            form_action,
            data=post_data,
            headers={"Referer": ENTRY_URL},
            timeout=30,
            allow_redirects=True,
        )
        r.raise_for_status()
        return r.text
