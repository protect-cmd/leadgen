from __future__ import annotations

import logging
import re
import string
import time
from datetime import date, datetime, timedelta

import requests
from bs4 import BeautifulSoup

from models.filing import Filing
from scrapers.dates import court_today

log = logging.getLogger(__name__)

STATE = "TN"
COUNTY = "Shelby"
COURT_TIMEZONE = "America/Chicago"

_SEARCH_URL = "https://gscivildata.shelbycountytn.gov/pls/gnweb/ck_public_qry_cpty.cp_personcase_details_idx"
_SETUP_URL = "https://gscivildata.shelbycountytn.gov/pls/gnweb/ck_public_qry_cpty.cp_personcase_setup_idx"

# Eviction case-type codes for Shelby.
#
# "06 - FED - LOCAL" was tested against live Shelby data on 2026-05-29 and
# returned zero filings across the validation window. Only "16 - FED - OTHER"
# currently appears active. We keep the list shape so a future re-activation
# of 06 (or any new FED code) is a one-line change.
_FED_CASE_TYPES = [
    "16 - FED - OTHER",
]

# Alphabet sweep: Contexte form requires last_name + supports partial-match checkbox.
# Single-letter with partial_ind=checked enumerates every defendant whose surname
# starts with that letter — confirmed against live data (letter "S" returned 20+
# DEFENDANT rows from SALIM through SCOTT in a 28-day window).
_ALPHABET = list(string.ascii_uppercase)

# Date format the Oracle PL/SQL form requires (e.g. "01-MAY-2026").
_DATE_FMT = "%d-%b-%Y"

# Address detection helpers — kept in module scope so they compile once.
_CITY_STATE_ZIP_RE = re.compile(r",?\s*[A-Z][A-Za-z .'-]+\s+[A-Z]{2}\s+\d{5}(-\d{4})?\s*$")
_STREET_START_RE = re.compile(r"^\d+\s+\S")

# Courtesy pause between letter sweeps so we don't hammer the county server.
_INTER_LETTER_SLEEP_SECONDS = 1.0

# Hard cap on pages fetched per letter — a safety net against runaway pagination.
_MAX_PAGES_PER_LETTER = 50


class ShelbyTNScraper:
    """
    Scrapes Shelby County (Memphis) General Sessions Civil court for eviction
    filings via the free public Neumo Contexte portal at gscivildata.shelbycountytn.gov.

    The form requires a last_name, so this scraper sweeps the 26 letters A-Z
    using the Partial Last Name checkbox to enumerate all defendants in the
    lookback window. Eviction filings are filtered server-side by case_type
    ("16 - FED - OTHER" is the only active code; "06 - FED - LOCAL" was tested
    and returns zero, so we don't query it).

    Defendant addresses are exposed inline on the search results list, so no
    case-detail navigation is needed. Output rows are filtered to party_type
    == DEFENDANT to drop attorneys, plaintiffs, and pro-se litigants. Filtering
    happens BEFORE dedup on case_number so a person who appears as defendant
    under one letter and attorney under another doesn't get the wrong row kept.
    """

    def __init__(self, lookback_days: int = 7, request_timeout: int = 30):
        self.lookback_days = lookback_days
        self.request_timeout = request_timeout
        self.last_error: str | None = None
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0"})

    def scrape(self) -> list[Filing]:
        self.last_error = None
        today = court_today(COURT_TIMEZONE)
        begin = today - timedelta(days=self.lookback_days)

        begin_str = begin.strftime(_DATE_FMT).upper()
        end_str = today.strftime(_DATE_FMT).upper()

        log.info(
            f"Shelby TN: scraping FED filings {begin_str} to {end_str} "
            f"({self.lookback_days}-day lookback)"
        )

        # Warm the session so the form POST has the same cookies a browser would.
        # The Contexte app sets a session cookie on the setup page that the
        # details endpoint validates loosely; skipping this works most of the
        # time but occasionally returns an empty results page.
        try:
            self.session.get(_SETUP_URL, timeout=self.request_timeout)
        except Exception as e:
            log.warning(f"Shelby TN: setup-page warm GET failed (non-fatal): {e}")

        filings: list[Filing] = []
        seen_cases: set[str] = set()

        for case_type in _FED_CASE_TYPES:
            for letter in _ALPHABET:
                try:
                    rows = self._search(letter, begin_str, end_str, case_type)
                except Exception as e:
                    log.warning(
                        f"Shelby TN: search failed for letter={letter} "
                        f"case_type={case_type}: {e}"
                    )
                    # Still take the courtesy pause so we don't slam the server
                    # with rapid retries when one letter is failing.
                    time.sleep(_INTER_LETTER_SLEEP_SECONDS)
                    continue

                # Courtesy pause between letters regardless of outcome.
                time.sleep(_INTER_LETTER_SLEEP_SECONDS)

                for row in rows:
                    # Party-type filter BEFORE dedup on case_number: a person
                    # can appear in multiple party roles on the same case (e.g.
                    # defendant under one letter, attorney under another).
                    # Filtering first guarantees we keep the DEFENDANT row.
                    if row["party_type"] != "DEFENDANT":
                        continue
                    if not row["case_number"]:
                        continue
                    if row["case_number"] in seen_cases:
                        continue
                    seen_cases.add(row["case_number"])

                    filings.append(Filing(
                        case_number=row["case_number"],
                        tenant_name=row["defendant"] or "Unknown",
                        property_address=row["address"] or "Unknown",
                        landlord_name=row["plaintiff"] or "Unknown",
                        filing_date=row["filing_date"],
                        court_date=row["filing_date"],
                        state=STATE,
                        county=COUNTY,
                        notice_type="Detainer Warrant",
                        source_url=_SETUP_URL,
                    ))

        log.info(f"Shelby TN: {len(filings)} eviction filings found")
        return filings

    def _search(
        self,
        last_name_letter: str,
        begin_str: str,
        end_str: str,
        case_type: str,
    ) -> list[dict]:
        """
        Sweep all result pages for one (letter, case_type) query.

        Termination conditions (any one stops the loop):
          (a) Server returns zero rows on the current page.
          (b) Server returns the same set of case numbers as the previous page
              — some ASP/Oracle deployments loop the last page instead of
              returning empty when PageNo overshoots.
          (c) Hard safety cap (_MAX_PAGES_PER_LETTER) — logged as a warning
              if hit, since exceeding it suggests something unexpected.

        We intentionally do NOT short-circuit on "short page" (e.g. fewer than
        N rows) because the server's page size has not been independently
        confirmed; using a row-count threshold risks silently dropping data.
        """
        rows: list[dict] = []
        previous_page_case_numbers: set[str] | None = None
        page = 1

        while page <= _MAX_PAGES_PER_LETTER:
            form = {
                "soundex_ind": "",          # phonetic search explicitly OFF
                "partial_ind": "checked",   # partial last-name match ON
                "last_name": last_name_letter,
                "first_name": "",
                "middle_name": "",
                "begin_date": begin_str,
                "end_date": end_str,
                "case_type": case_type,
                "PageNo": str(page),
            }

            r = self.session.post(
                _SEARCH_URL,
                data=form,
                timeout=self.request_timeout,
            )
            r.raise_for_status()

            page_rows = _parse_results_page(r.text)

            # (a) Empty page — natural end of results.
            if not page_rows:
                break

            current_case_numbers = {
                row["case_number"]
                for row in page_rows
                if row["case_number"]
            }

            # (b) Server looped the last page back to us — stop, do NOT append
            # the duplicate rows.
            if previous_page_case_numbers == current_case_numbers:
                log.info(
                    f"Shelby TN: letter={last_name_letter} "
                    f"case_type={case_type} page={page} "
                    f"repeated prior page's case set; terminating sweep."
                )
                break

            previous_page_case_numbers = current_case_numbers
            rows.extend(page_rows)

            log.info(
                f"Shelby TN: letter={last_name_letter} "
                f"case_type={case_type} page={page} "
                f"rows_in_page={len(page_rows)} total_rows={len(rows)}"
            )

            page += 1

        # (c) Hard cap hit — surface a warning so anomalies don't go silent.
        if page > _MAX_PAGES_PER_LETTER:
            log.warning(
                f"Shelby TN: pagination safety cap hit "
                f"(>{_MAX_PAGES_PER_LETTER} pages) for letter={last_name_letter} "
                f"case_type={case_type} — investigate before relying on this "
                f"letter's data."
            )

        return rows


def _parse_results_page(html: str) -> list[dict]:
    """
    Parse a Contexte results page into structured rows.

    The results table has one logical row per (person, case) tuple with columns:
      ID | Name/Corporation | Address | Party Type | Filing Date

    Address can include both street and city/state/ZIP. Some rows have multiple
    text lines collapsed into a single <td>.
    """
    rows: list[dict] = []

    soup = BeautifulSoup(html, "html.parser")
    table = _find_results_table(soup)
    if table is None:
        return rows

    for tr in table.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 5:
            continue

        # Skip header rows (look for "ID" / "Name" header text).
        first = cells[0].get_text(strip=True).upper()
        if first in ("ID", ""):
            continue

        name_cell_text = _cell_lines(cells[1])
        address_cell_text = _cell_lines(cells[2])
        party_type = cells[3].get_text(strip=True).upper()
        filing_date_str = cells[4].get_text(strip=True)

        case_number, plaintiff_v_defendant = _extract_case_line(name_cell_text)
        defendant = name_cell_text[0] if name_cell_text else ""

        # The caption is formatted "PLAINTIFF V DEFENDANT" — split on " V ".
        plaintiff, _ = _split_caption(plaintiff_v_defendant)

        address = _join_address(address_cell_text)

        try:
            filing_date = datetime.strptime(filing_date_str, "%d-%b-%Y").date()
        except ValueError:
            continue  # malformed date — drop the row rather than crash

        rows.append({
            "case_number": case_number,
            "defendant": _clean_name(defendant),
            "plaintiff": _clean_name(plaintiff),
            "address": address,
            "party_type": party_type,
            "filing_date": filing_date,
        })

    return rows


def _find_results_table(soup: BeautifulSoup):
    """The Contexte results page has one main table; locate it heuristically."""
    for table in soup.find_all("table"):
        text = table.get_text(" ", strip=True).upper()
        if "PARTY TYPE" in text and "FILING DATE" in text:
            return table
    return None


def _cell_lines(cell) -> list[str]:
    """Get the text lines from a <td>, preserving line breaks as separators."""
    # Replace <br> tags with newlines before extracting text.
    for br in cell.find_all("br"):
        br.replace_with("\n")
    raw = cell.get_text("\n", strip=False)
    return [ln.strip() for ln in raw.split("\n") if ln.strip()]


def _extract_case_line(lines: list[str]) -> tuple[str, str]:
    """
    The name cell contains the defendant name on line 1 and a "Case: <num>  <caption>"
    line below. Pull out the case number and caption text.
    """
    for line in lines:
        m = re.match(r"^\s*Case:\s*(\d+)\s+(.*?)\s*$", line, flags=re.IGNORECASE)
        if m:
            return m.group(1), m.group(2)
    return "", ""


def _split_caption(caption: str) -> tuple[str, str]:
    """Split 'PLAINTIFF V DEFENDANT' on the literal ' V ' separator."""
    if not caption:
        return "", ""
    # Match " V " or " VS " surrounded by spaces, case-insensitive.
    parts = re.split(r"\s+VS?\s+", caption, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return caption.strip(), ""


def _join_address(lines: list[str]) -> str:
    """
    Address cell may have street on one line and city/state/ZIP on another,
    or both collapsed onto one line. Stitch into a single comma-separated string.
    """
    if not lines:
        return ""
    # Drop the "Case:" continuation line if it leaked into the address column.
    filtered = [ln for ln in lines if not ln.upper().startswith("CASE:")]
    return ", ".join(filtered)


def _clean_name(name: str) -> str:
    """Strip trailing junk like 'OR ALL OCCUPANTS', 'OR OCCU' (truncated by
    Contexte), '& ETAL', '/OCCUPANTS' from names."""
    if not name:
        return ""
    # Match OR + (ALL|OCCU...), /OCCUPANTS, or & ETAL — any of these and everything after.
    name = re.sub(
        r"\s*(OR\s+(ALL\s+OCCUPANTS?|OCCU\w*)|/OCCUPANTS?|&\s+ETAL).*$",
        "",
        name,
        flags=re.IGNORECASE,
    )
    name = re.sub(r",\s*$", "", name)
    return name.strip()