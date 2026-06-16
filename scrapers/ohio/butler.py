from __future__ import annotations

import logging
import math
import random
import re
import time
from datetime import date, datetime, timedelta

import requests
from bs4 import BeautifulSoup

from models.filing import Filing
from scrapers.dates import court_today
from services.name_utils import clean_tenant_name

log = logging.getLogger(__name__)

STATE = "OH"
COUNTY = "Butler"
COURT_TIMEZONE = "America/New_York"
NOTICE_TYPE = "Eviction"

BASE_URL = "https://docket.bcohio.gov"
SEARCH_URL = f"{BASE_URL}/recordSearch.php"

# Butler County Area codes: Area 1 = 0910, Area 2 = 0911, Area 3 = 0912
AREA_CODES = ["0910", "0911", "0912"]
_AREA_LABEL = {"0910": "Area 1", "0911": "Area 2", "0912": "Area 3"}
_COURT_CODE = "0999"

# Courtesy delay between individual HTTP fetches
_DELAY_MIN = 0.5
_DELAY_MAX = 1.5

_OCCUPANT_SUFFIXES = re.compile(
    r"\s+(et\.?\s*al\.?|and\s+all\s+(?:other\s+)?(?:occupants?|tenants?|persons?|others?))$",
    flags=re.IGNORECASE,
)


def _strip_occupant_suffix(name: str) -> str:
    return _OCCUPANT_SUFFIXES.sub("", name).strip()


def _parse_results_page(html: str) -> tuple[list[dict], int]:
    """
    Parse one page of Calendar Search results.

    Returns:
        stubs   – list of dicts (case_number, tenant_raw, court_date, source_url)
                  for CVG (eviction) cases only
        total   – total match count reported by the portal ("N matches were found")
    """
    soup = BeautifulSoup(html, "html.parser")

    # Parse total match count: "N matches were found (250 displayed)"
    total = 0
    m = re.search(r"(\d+)\s+matches? were found", soup.get_text(" "), re.IGNORECASE)
    if m:
        total = int(m.group(1))

    stubs: list[dict] = []
    for card in soup.select(".record"):
        case_el = card.select_one(".fullCaseNumber")
        if not case_el:
            continue
        case_num = case_el.get_text(strip=True)
        if not case_num.startswith("CVG"):
            continue

        # Tenant name — strip "Concerning: " prefix
        tenant_raw = ""
        concerning = card.select_one(".concerningName")
        if concerning:
            text = concerning.get_text(strip=True)
            tenant_raw = re.sub(r"(?i)^Concerning:\s*", "", text)

        # Hearing / court date
        court_date_val: date | None = None
        hd_el = card.select_one(".hearingDate")
        if hd_el:
            hd_m = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", hd_el.get_text())
            if hd_m:
                try:
                    court_date_val = datetime.strptime(hd_m.group(1), "%m/%d/%Y").date()
                except ValueError:
                    pass

        # Case detail link
        link_el = card.select_one(".caseLink a") or card.find(
            "a", href=re.compile(r"caseMulti")
        )
        source_url = ""
        if link_el:
            href = link_el.get("href", "")
            source_url = (
                href if href.startswith("http") else f"{BASE_URL}/{href.lstrip('/')}"
            )

        stubs.append(
            {
                "case_number": case_num,
                "tenant_raw": tenant_raw,
                "court_date": court_date_val,
                "source_url": source_url,
            }
        )

    return stubs, total


def _fetch_case_detail(
    session: requests.Session, detail_url: str
) -> tuple[str | None, str | None, date | None]:
    """
    GET a case detail page and return (landlord_name, property_address, filing_date).
    Returns (None, None, None) on any error.

    Case detail table layout (confirmed from live portal):
        Each party row is a single <tr> with many <td> cells.
        cells[0]  = role + name (e.g. "Plaintiff 1:\\nHazel Valley Homes\\n...")
        cells[1]  = name
        cells[5]  = street address
        cells[7]  = C/S/Z (city, state zip)
    """
    try:
        r = session.get(detail_url, timeout=15)
        r.raise_for_status()
    except Exception as exc:
        log.warning("Butler: case detail fetch failed for %s: %s", detail_url, exc)
        return None, None, None

    soup = BeautifulSoup(r.text, "html.parser")
    page_text = soup.get_text("\n")

    landlord: str | None = None
    address: str | None = None
    filing_date: date | None = None

    # Primary: parse party table rows
    for tr in soup.find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if not cells:
            continue
        first = cells[0]

        if first.startswith("Plaintiff 1:") and len(cells) > 1:
            name = cells[1].strip()
            if name:
                landlord = name

        if first.startswith("Defendant 1:") and len(cells) >= 8:
            street = cells[5].strip() if len(cells) > 5 else ""
            csz = cells[7].strip() if len(cells) > 7 else ""
            if street and csz:
                address = f"{street}, {csz}"

    # Fallback: regex on plain text (handles alternate layouts)
    if not address:
        def_m = re.search(
            r"Defendant 1:(.*?)(?:Miscellaneous|$)", page_text, re.DOTALL | re.IGNORECASE
        )
        if def_m:
            sec = def_m.group(1)
            a_m = re.search(r"Address:\s*(.+)", sec)
            c_m = re.search(r"C/S/Z:\s*(.+)", sec)
            if a_m and c_m:
                address = f"{a_m.group(1).strip()}, {c_m.group(1).strip()}"

    # Filing date
    fd_m = re.search(r"Filing Date:\s*(\d{1,2}/\d{1,2}/\d{4})", page_text)
    if fd_m:
        try:
            filing_date = datetime.strptime(fd_m.group(1), "%m/%d/%Y").date()
        except ValueError:
            pass

    log.debug(
        "Butler: case detail → landlord=%s addr=%s filing_date=%s",
        landlord,
        address,
        filing_date,
    )
    return landlord, address, filing_date


class ButlerCountyAreaCourtScraper:
    """
    Scraper for Butler County Area Courts (OH) eviction filings.

    Portal: https://docket.bcohio.gov (Henschen & Associates CaseLook PHP)

    Session flow:
        1. GET /recordSearch.php → disclaimer; parse Continue ?k=acceptAgreement… href
        2. GET acceptAgreement URL → form page; parse Calendar Search tab href
        3. GET Calendar Search URL → form; parse hidden <input name="k"> value
        4. POST /recordSearch.php per area (0910 / 0911 / 0912) with Calendar Search fields
        5. Paginate via GET ?k=page0999<session>&p=<N>  (N is 0-indexed, page 2 = p=1)
        6. GET ?k=caseMulti<area><session><id> per CVG case → landlord + address + filing date

    Date fix (Zee's review):
        court_date  = actual hearing date parsed from the results card (.hearingDate)
        filing_date = actual filing date parsed from the case detail page ("Filing Date:")
        Neither is ever stamped as today's date.
    """

    def __init__(self, lookback_days: int = 2, lookahead_days: int = 30):
        self.lookback_days = lookback_days
        self.lookahead_days = lookahead_days
        self.last_error: str | None = None
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Referer": BASE_URL,
            }
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scrape(self) -> list[Filing]:
        today = court_today(COURT_TIMEZONE)
        start = today - timedelta(days=self.lookback_days)
        end = today + timedelta(days=self.lookahead_days)

        form_k = self._ensure_session()
        if not form_k:
            self.last_error = "Butler: could not establish session"
            log.error(self.last_error)
            return []

        filings: list[Filing] = []
        seen: set[str] = set()
        area_errors: list[str] = []

        for area_code in AREA_CODES:
            try:
                area_filings = self._scrape_area(form_k, area_code, start, end, today)
            except Exception as exc:
                msg = f"Butler {_AREA_LABEL[area_code]}: unexpected error: {exc}"
                area_errors.append(msg)
                log.error(
                    "Butler %s: unexpected error: %s",
                    _AREA_LABEL[area_code],
                    exc,
                    exc_info=True,
                )
                continue
            for f in area_filings:
                if f.case_number not in seen:
                    seen.add(f.case_number)
                    filings.append(f)

        self.last_error = area_errors[-1] if area_errors and not filings else None
        log.info("Butler OH: %d eviction filings total", len(filings))
        return filings

    # ------------------------------------------------------------------
    # Session establishment
    # ------------------------------------------------------------------

    def _ensure_session(self) -> str | None:
        """
        Walk through the Henschen disclaimer → acceptAgreement → Calendar Search form.
        Returns the value of the hidden <input name="k"> on the Calendar Search form,
        or None if any step fails.
        """
        try:
            # Step 1: GET disclaimer page
            r = self.session.get(SEARCH_URL, timeout=15)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")

            # Step 2: Parse "Continue" link  (href contains "acceptAgreement")
            cont = soup.find("a", href=lambda h: h and "acceptAgreement" in h)
            if not cont:
                log.error("Butler: no acceptAgreement link on disclaimer page")
                return None
            href = cont["href"]
            accept_url = href if href.startswith("http") else f"{BASE_URL}/{href.lstrip('/')}"

            # Step 3: GET acceptAgreement → page with search tabs
            r2 = self.session.get(accept_url, timeout=15)
            r2.raise_for_status()
            soup2 = BeautifulSoup(r2.text, "html.parser")

            # Step 4: Parse Calendar Search tab link
            cal = soup2.find("a", string=re.compile(r"Calendar\s*Search", re.IGNORECASE))
            if not cal:
                log.error("Butler: Calendar Search tab not found")
                return None
            cal_href = cal["href"]
            cal_url = (
                cal_href
                if cal_href.startswith("http")
                else f"{BASE_URL}/{cal_href.lstrip('/')}"
            )

            # Step 5: GET Calendar Search form
            r3 = self.session.get(cal_url, timeout=15)
            r3.raise_for_status()
            soup3 = BeautifulSoup(r3.text, "html.parser")

            # Step 6: Parse hidden k input
            k_inp = soup3.find("input", {"name": "k"})
            if not k_inp or not k_inp.get("value"):
                log.error("Butler: hidden k input not found on Calendar Search form")
                return None

            form_k = k_inp["value"]
            log.debug("Butler: session ready (form_k=%.24s…)", form_k)
            return form_k

        except Exception as exc:
            log.error("Butler: session setup failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Area search + pagination + detail fetches
    # ------------------------------------------------------------------

    def _scrape_area(
        self,
        form_k: str,
        area_code: str,
        start: date,
        end: date,
        today: date,
    ) -> list[Filing]:
        """Search one area, paginate, fetch case details, return CVG filings.

        IMPORTANT: The Henschen portal resets its session context on every
        pagination GET (?k=page0999…).  Any case-detail URL captured before
        that GET becomes invalid (returns a 2557-byte stub page instead of the
        full detail).  Fix: fetch details for each page's stubs immediately,
        before issuing the next pagination GET.
        """
        area_label = _AREA_LABEL[area_code]

        # Derive session key for pagination.
        # All k values embed the court code followed by the session: <prefix>0999<session>
        sm = re.search(r"0999(\w+)", form_k)
        session = sm.group(1) if sm else ""

        # POST Calendar Search
        payload: dict[str, str] = {
            "": "MU",
            "searchType": "fileDate",
            "k": form_k,
            "searchBMonth": str(start.month),
            "searchBDay": str(start.day),
            "searchBYear": str(start.year),
            "searchEMonth": str(end.month),
            "searchEDay": str(end.day),
            "searchEYear": str(end.year),
            "searchAgency[]": area_code,
            "searchBlock": "250",
        }

        r = self.session.post(
            SEARCH_URL,
            data=payload,
            headers={"Referer": SEARCH_URL},
            timeout=30,
        )
        r.raise_for_status()

        stubs, total = _parse_results_page(r.text)
        log.debug(
            "Butler %s: %d total records, %d CVG on page 1",
            area_label,
            total,
            len(stubs),
        )

        # Fetch details for page 1 stubs BEFORE any pagination GET.
        filings = self._build_filings(stubs, today)

        # Paginate remaining pages (page 2 = p=1, page 3 = p=2, …)
        # After each pagination GET, immediately fetch that page's details
        # before the next GET resets the server's session context.
        if total > 250 and session:
            num_pages = math.ceil(total / 250)
            for page_idx in range(1, num_pages):
                time.sleep(random.uniform(_DELAY_MIN, _DELAY_MAX))
                page_url = f"{SEARCH_URL}?k=page{_COURT_CODE}{session}&p={page_idx}"
                try:
                    pr = self.session.get(page_url, timeout=30)
                    pr.raise_for_status()
                    page_stubs, _ = _parse_results_page(pr.text)
                    log.debug(
                        "Butler %s: page %d -> +%d CVG",
                        area_label,
                        page_idx + 1,
                        len(page_stubs),
                    )
                    filings.extend(self._build_filings(page_stubs, today))
                except Exception as exc:
                    log.warning(
                        "Butler %s: pagination p=%d failed: %s",
                        area_label,
                        page_idx,
                        exc,
                    )
                    break

        log.info("Butler %s: %d eviction filings", area_label, len(filings))
        return filings

    def _build_filings(self, stubs: list[dict], today: date) -> list[Filing]:
        """Fetch case detail for each stub and return Filing objects.

        Called once per pagination page so that detail GETs happen before
        the next pagination GET resets the server's session context.
        """
        filings: list[Filing] = []
        for stub in stubs:
            time.sleep(random.uniform(_DELAY_MIN, _DELAY_MAX))
            landlord, address, filing_date = _fetch_case_detail(
                self.session, stub["source_url"]
            )
            tenant = _strip_occupant_suffix(stub["tenant_raw"])
            filings.append(
                Filing(
                    case_number=stub["case_number"],
                    tenant_name=clean_tenant_name(tenant) or "Unknown",
                    property_address=address or "Unknown",
                    landlord_name=landlord or "Unknown",
                    filing_date=filing_date or stub["court_date"] or today,
                    court_date=stub["court_date"],
                    state=STATE,
                    county=COUNTY,
                    notice_type=NOTICE_TYPE,
                    source_url=stub["source_url"],
                )
            )
        return filings
