from __future__ import annotations

import logging
import random
import re
import time
from datetime import date, timedelta

import requests
from bs4 import BeautifulSoup  # still used by _parse_case_rows and _fetch_defendant_address

from models.filing import Filing
from scrapers.dates import court_today
from services.name_utils import clean_tenant_name

log = logging.getLogger(__name__)

STATE = "OH"
COUNTY = "Lorain"
COURT_TIMEZONE = "America/New_York"
NOTICE_TYPE = "Eviction"

BASE_URL = "https://eservices.elyriamunicourt.org"
ESERVICES_ROOT = f"{BASE_URL}/eservices/"
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


def _playwright_search(begin: date, end: date) -> tuple[list[dict], str]:
    """
    Full Playwright-based eviction search on the Elyria Municipal Court portal.

    The portal's /eservices/ root serves a React SPA. The React home page
    renders a "Case Search" card; clicking it navigates via /eservices/casesearch
    to the Wicket search.page (with session ?x= token). The Case Type tab is
    AJAX-driven so it cannot be triggered via plain HTTP — Playwright handles
    the full flow.

    Session flow:
        1. GET /eservices/  (React SPA home)
        2. Wait for React to render "Case Search" card
        3. Click "Click Here" on the Case Search card
        4. Follow redirect to search.page?x=TOKEN
        5. Click "Case Type" tab (AJAX)
        6. Fill Begin/End date + select Eviction (CVG)
        7. Submit form (input[name=submitLink])
        8. Collect results HTML; paginate via ">" link
        9. Return (rows, jsessionid) — JSESSIONID reused for detail fetches

    Returns (rows, jsessionid).
    Raises RuntimeError if navigation or form submission fails.
    """
    from playwright.sync_api import sync_playwright  # noqa: PLC0415
    from playwright.sync_api import TimeoutError as PWTimeout  # noqa: PLC0415

    begin_str = begin.strftime("%m/%d/%Y")
    end_str = end.strftime("%m/%d/%Y")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        page = ctx.new_page()
        try:
            # Step 1+2: Load React home, wait for Case Search card
            page.goto(ESERVICES_ROOT, wait_until="networkidle", timeout=30_000)
            try:
                page.wait_for_selector(
                    "text=Case Search", timeout=15_000, state="visible"
                )
            except PWTimeout:
                raise RuntimeError(
                    f"Lorain: React home did not render Case Search card (url={page.url})"
                )

            # Step 3: Click "Click Here" on the Case Search card
            try:
                page.click("button:has-text('Click Here')", timeout=5_000)
            except PWTimeout:
                page.click("text=Click Here", timeout=5_000)
            page.wait_for_url("**/search.page**", timeout=15_000)
            page.wait_for_load_state("networkidle", timeout=15_000)
            log.debug("Lorain: search page URL: %s", page.url)

            # Step 4: Click Case Type tab (AJAX)
            page.click("text=Case Type", timeout=10_000)
            page.wait_for_timeout(1_000)

            # Step 5: Fill date range and case type
            page.fill("input[name='fileDateRange:dateInputBegin']", begin_str)
            page.fill("input[name='fileDateRange:dateInputEnd']", end_str)
            page.select_option("select[name='caseCd']", label="Eviction (CVG)")
            page.wait_for_timeout(300)

            # Step 6: Submit
            page.click("input[name='submitLink']", timeout=5_000)
            page.wait_for_load_state("networkidle", timeout=15_000)
            page.wait_for_timeout(500)

            # Step 7: Collect all result pages
            _MAX_PAGES = 20
            all_rows: list[dict] = []
            page_num = 1

            while True:
                rows = _parse_case_rows(page.content())
                all_rows.extend(rows)
                log.debug(
                    "Lorain: page %d → %d rows (total: %d)",
                    page_num, len(rows), len(all_rows),
                )

                if page_num >= _MAX_PAGES:
                    break

                # Use a locator (lazy, never goes stale) to find the ">" next-page link.
                # The portal uses Wicket AJAX pagination — the URL stays the same
                # (searchresults.page) and only the table content updates.
                next_locator = page.locator("a", has_text=re.compile(r"^\s*>\s*$"))
                if next_locator.count() == 0:
                    break

                # Record the first case number so we can detect when AJAX updates the table.
                first_case_before = page.evaluate(
                    "() => { const a = document.querySelector('td a[href*=\"searchresults\"]');"
                    " return a ? a.innerText.trim() : ''; }"
                )

                next_locator.first.click()

                if first_case_before:
                    # Wait for the first case number in the table to change (AJAX loaded).
                    try:
                        page.wait_for_function(
                            "(prev) => { const a = document.querySelector('td a[href*=\"searchresults\"]');"
                            " return a ? a.innerText.trim() !== prev : false; }",
                            arg=first_case_before,
                            timeout=10_000,
                        )
                    except PWTimeout:
                        log.debug("Lorain: table unchanged after '>' click — end of results")
                        break
                else:
                    page.wait_for_load_state("networkidle", timeout=15_000)
                    page.wait_for_timeout(500)

                page_num += 1

            cookies = ctx.cookies()
            jsessionid = next(
                (c["value"] for c in cookies if c["name"] == "JSESSIONID"), ""
            )

        finally:
            browser.close()

    log.info("Lorain: %d party rows across %d page(s)", len(all_rows), page_num)
    return all_rows, jsessionid


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

    The portal serves a React SPA at /eservices/ (root). The React app renders
    a "Case Search" card that navigates via /eservices/casesearch to the Wicket
    search.page. The Case Type tab is AJAX-driven and cannot be triggered via
    plain HTTP — Playwright handles the full search flow.

    Session flow (see _playwright_search for details):
        1.   GET /eservices/                 → React SPA home
        2.   Wait for React "Case Search" card to appear
        3.   Click "Click Here"              → redirect to search.page?x=TOKEN
        4.   Click Case Type tab (AJAX)
        5.   POST date range + Eviction CVG  → searchresults.page
        6.   Paginate via ">" nav link       → collect all party rows
        7.   GET searchresults.page?x=... per case → defendant address
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
        Run the full Playwright-based search and store the JSESSIONID for
        subsequent detail-page fetches via self.session.

        Playwright sync API cannot run inside an asyncio event loop (e.g. when
        called from run_ohio.py's async main).  If a loop is already running we
        dispatch to a ThreadPoolExecutor so Playwright gets a loop-free thread.
        """
        import asyncio
        import concurrent.futures

        try:
            asyncio.get_running_loop()
            in_loop = True
        except RuntimeError:
            in_loop = False

        if in_loop:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                rows, jsessionid = ex.submit(_playwright_search, begin, end).result()
        else:
            rows, jsessionid = _playwright_search(begin, end)

        if jsessionid:
            self.session.cookies.set(
                "JSESSIONID", jsessionid,
                domain=BASE_URL.replace("https://", ""),
                path="/",
            )
        return rows
