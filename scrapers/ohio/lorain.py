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
HOME_PAGE_URL = f"{BASE_URL}/eservices/home.page"
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
        # Make absolute — Wicket result links come back as relative ?x=... tokens
        if href.startswith("http"):
            pass
        elif href.startswith("?"):
            href = SEARCH_RESULTS_URL + href
        else:
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

    # CourtView detail page splits addresses across 5 separate lines:
    #   Street
    #   City
    #   ,
    #   ST
    #   XXXXX
    # Find the first "- Defendant" section, then collect the address parts.
    in_defendant = False
    for i, line in enumerate(lines):
        if line == "- Defendant":
            in_defendant = True
        if in_defendant and line.upper() == "ADDRESS":
            # Collect non-empty lines until we reach the zip code or a section break.
            parts = []
            _section_breaks = {"Alias", "Party Attorney", "Events", "Docket", "Financial"}
            for j in range(i + 1, min(i + 40, len(lines))):
                part = lines[j].strip()
                if not part:
                    continue
                if part in _section_breaks or part.startswith("- "):
                    break
                if part == ",":
                    continue  # skip standalone comma separator
                parts.append(part)
                if re.match(r"^\d{5}", part):
                    break  # zip reached — done
            # Expect: [street, city, state_abbr, zip_code]
            if len(parts) >= 4:
                street, city, state_abbr, zip_code = parts[0], parts[1], parts[2], parts[3]
                if re.match(r"^[A-Z]{2}$", state_abbr) and re.match(r"^\d{5}", zip_code):
                    return f"{street}, {city}, {state_abbr} {zip_code}"
            if len(parts) >= 2:
                return f"{parts[0]}, {' '.join(parts[1:])}"
            break

    log.warning("Lorain: no defendant address found at %s", detail_url)
    return None


class ElyriaMunicipalScraper:
    """
    Scraper for Elyria Municipal Court (Lorain County OH) eviction filings.

    Portal: https://eservices.elyriamunicourt.org/eservices/ (CourtView v1.55 / equivant)

    This is a stateful CourtView/equivant Java (Apache Wicket) app.
    home.page is the required entry point — it issues the JSESSIONID and the
    initial x= page token that every subsequent Wicket URL must carry.
    Skipping home.page and going directly to a deeper page returns a formless
    stub because Wicket has no server-side state for the request.

    Session flow:
        1. GET /eservices/home.page          → sets JSESSIONID; page contains
                                               nav link to search.page?x=TOKEN
        2. Follow search.page?x=TOKEN        → Name search form with tabs
        3. Find "Case Type" tab → GET its ?x=... link → Case Type form
           Extract exact caseCd value for "Eviction (CVG)" from the <select>
           (padded to 30 chars server-side; bare "CVG" defaults to CVF)
        4. POST caseCd + date range          → searchresults.page
        5. Paginate via ">" nav link         → collect all party rows
        6. GET searchresults.page?x=... per case → defendant address
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
            rows = self._search(begin, end)
        except Exception as exc:
            msg = f"Lorain: search failed for {begin.isoformat()}–{end.isoformat()}: {exc}"
            fetch_errors.append(msg)
            log.error("Lorain: search failed for %s–%s: %s", begin.isoformat(), end.isoformat(), exc)
            self.last_error = msg
            return []

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

    def _search(self, begin: date, end: date) -> list[dict]:
        """
        Execute the CourtView session flow, POST the CVG date-range query,
        then paginate through all result pages via the Wicket '>' nav link.

        Returns a flat list of row dicts from _parse_case_rows() across all pages.
        The portal caps results at 200 records; each page shows ~25 rows.
        """
        begin_str = begin.strftime("%m/%d/%Y")
        end_str = end.strftime("%m/%d/%Y")

        # Step 1: GET home.page — establishes JSESSIONID and the initial Wicket
        # x= page token.  Without this, any deeper page returns a formless stub
        # because Wicket has no server-side state for the request.
        r = self.session.get(HOME_PAGE_URL, timeout=15, allow_redirects=True)
        r.raise_for_status()
        if not self.session.cookies.get("JSESSIONID"):
            raise RuntimeError(
                f"Lorain: JSESSIONID not set after home.page (url={r.url})"
            )
        soup = BeautifulSoup(r.text, "html.parser")

        # Step 2: Navigate to the Case Search / Name search form.
        # home.page contains a nav link to search.page?x=TOKEN.
        # If home.page is already the search form (some CourtView installs skip
        # the landing page), use it directly.
        if not soup.find("form"):
            search_link = soup.find(
                "a", href=re.compile(r"search\.page", re.IGNORECASE)
            )
            if not search_link:
                # Broader fallback: any ?x= link on the page carries the token
                search_link = soup.find(
                    "a", href=lambda h: h and "?x=" in h
                )
            if not search_link:
                raise RuntimeError(
                    f"Lorain: no link to search form found on home.page "
                    f"({len(r.text)} bytes, url={r.url})"
                )
            search_href = search_link["href"]
            if not search_href.startswith("http"):
                search_href = f"{BASE_URL}/eservices/{search_href.lstrip('/')}"

            time.sleep(0.4)
            r = self.session.get(search_href, timeout=15, allow_redirects=True)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")

        # Step 3: Find "Case Type" tab within the search page.
        # The Name tab is the default; Case Type has the date-range form.
        case_type_link = next(
            (a["href"] for a in soup.find_all("a", href=True)
             if a.get_text(strip=True) == "Case Type"),
            None,
        )
        if not case_type_link:
            raise RuntimeError(
                f"Lorain: could not find 'Case Type' tab in search form "
                f"({len(r.text)} bytes, url={r.url})"
            )
        case_type_url = r.url.split("?")[0] + case_type_link

        # Step 4: GET Case Type search form
        time.sleep(0.4)
        r = self.session.get(case_type_url, timeout=15, allow_redirects=True)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        form = soup.find("form")
        if not form:
            raise RuntimeError(
                f"Lorain: no form on Case Type search page "
                f"({len(r.text)} bytes, url={r.url})"
            )

        # Extract form action (may be relative)
        form_action = form.get("action", "")
        if not form_action.startswith("http"):
            form_action = r.url.split("?")[0] + form_action

        # Extract Wicket hidden field (name varies per session, e.g. id72_hf_0)
        hidden = form.find("input", {"type": "hidden"})
        if not hidden or not hidden.get("name"):
            raise RuntimeError("Lorain: no Wicket hidden field in Case Type form")
        hidden_name = hidden["name"]
        hidden_value = hidden.get("value", "")

        # Extract the exact caseCd value for "Eviction (CVG)" — padded to 30 chars
        # Sending bare 'CVG' defaults to the first option (CVF).
        caseCd_select = form.find("select", {"name": "caseCd"})
        cvg_option = (
            caseCd_select.find("option", string=re.compile(r"Eviction"))
            if caseCd_select
            else None
        )
        cvg_value = cvg_option["value"] if cvg_option else "CVG"
        if not cvg_option:
            log.warning("Lorain: could not find Eviction option in caseCd select; using 'CVG'")

        # Step 5: POST Case Type form with date range → first results page
        post_data = {
            hidden_name: hidden_value,
            "fileDateRange:dateInputBegin": begin_str,
            "fileDateRange:dateInputEnd": end_str,
            "caseCd": cvg_value,
            "submitLink": "Search",
        }
        time.sleep(0.5)
        r = self.session.post(
            form_action,
            data=post_data,
            headers={"Referer": r.url},
            timeout=30,
            allow_redirects=True,
        )
        r.raise_for_status()

        # Paginate through all result pages via Wicket '>' nav link.
        _MAX_PAGES = 20  # safety cap; portal caps at ~200 records (~8 pages)
        all_rows: list[dict] = []
        page_num = 1
        html = r.text

        while True:
            rows = _parse_case_rows(html)
            all_rows.extend(rows)
            log.debug("Lorain: page %d → %d rows (running total: %d)", page_num, len(rows), len(all_rows))

            soup = BeautifulSoup(html, "html.parser")
            next_href = next(
                (a["href"] for a in soup.find_all("a", href=True)
                 if a.get_text(strip=True) == ">"),
                None,
            )
            if not next_href:
                break
            if page_num >= _MAX_PAGES:
                log.warning("Lorain: pagination safety cap (%d pages) reached; stopping early", _MAX_PAGES)
                break

            time.sleep(0.4)
            r = self.session.get(SEARCH_RESULTS_URL + next_href, timeout=15, allow_redirects=True)
            r.raise_for_status()
            html = r.text
            page_num += 1

        log.info("Lorain: %d party rows across %d page(s)", len(all_rows), page_num)
        return all_rows
