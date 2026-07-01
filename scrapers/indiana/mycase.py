from __future__ import annotations

"""
Indiana statewide eviction scraper — public.courts.in.gov/mycase

Portal: Tyler Technologies Odyssey (Angular SPA).
API:    public.courts.in.gov/mycase/Search/SearchCases (POST JSON)
        public.courts.in.gov/mycase/Case/CaseSummary  (GET JSON)

No browser / Playwright needed. Session cookie is set on the first GET
and reused for all subsequent API calls via requests.Session.

CourtItemID 92 = Indiana statewide (all counties).

Clients:
  VDG  — mode="filings"   — new EV filings in the last N days (default)
  ISTS — mode="judgments" — EV cases with a judgment entry (TBD — stub only)

Pagination note: TotalResults is capped at 1001 by the portal. If we hit
the cap, the date window is bisected recursively until each sub-window
returns fewer than 1001 results. Single-day windows that still hit the cap
are paginated in full (up to 1001 results).

Session note: the portal session cookie expires after ~5–7 minutes. Because
the search phase can take several minutes for a 7-day statewide window, the
session is automatically refreshed on a 403 during detail fetches.

Rate limiting note: the portal WAF blocks IPs that send too many requests too
fast. Detail fetches are throttled to 2–4 s each. If a search POST returns 403,
the scraper waits _SEARCH_403_WAIT seconds, refreshes the session, and retries
once before aborting. For local testing keep lookback_days <= 2.
"""

import asyncio
import logging
import random
import re
import time
from datetime import date, datetime, timedelta

import requests

from models.filing import Filing
from scrapers.dates import court_today
from services.name_utils import clean_tenant_name

log = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────── #

_BASE_URL       = "https://public.courts.in.gov/mycase/"
_SEARCH_URL     = "https://public.courts.in.gov/mycase/Search/SearchCases"
_SUMMARY_URL    = "https://public.courts.in.gov/mycase/Case/CaseSummary"

STATE           = "IN"
COURT_TIMEZONE  = "America/Indiana/Indianapolis"
NOTICE_TYPE     = "Eviction"

_STATEWIDE_COURT_ID = 92

_DEFENDANT_CODE = 2   # Party Connection: Tenant
_PLAINTIFF_CODE = 3   # Party Connection: Landlord

_EV_CASE_TYPE   = "EV - Evictions (Small Claims Docket)"

_PAGE_SIZE          = 200
_RESULTS_CAP        = 1001
_SEARCH_403_WAIT    = 45   # seconds to wait before retrying a blocked search
_DETAIL_DELAY_MIN   = 2.0  # seconds between detail fetches (min)
_DETAIL_DELAY_MAX   = 4.0  # seconds between detail fetches (max)
_SEARCH_DELAY_MIN   = 0.5  # seconds between search pages
_SEARCH_DELAY_MAX   = 1.2

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Headers for the initial page GET — looks like a browser navigation
_INIT_HEADERS = {
    "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest":            "document",
    "Sec-Fetch-Mode":            "navigate",
    "Sec-Fetch-Site":            "none",
    "Sec-Fetch-User":            "?1",
}

# Headers for AJAX calls (search + detail)
_AJAX_HEADERS_BASE = {
    "Origin":         "https://public.courts.in.gov",
    "Referer":        _BASE_URL,
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

_DETAIL_HEADERS = {
    **_AJAX_HEADERS_BASE,
    "Accept":           "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}

_SEARCH_HEADERS = {
    **_AJAX_HEADERS_BASE,
    "Accept":           "application/json, text/javascript, */*; q=0.01",
    "Content-Type":     "application/json",
    "X-Requested-With": "XMLHttpRequest",
}


class _PortalBlockedError(Exception):
    """Raised when the portal serves a CAPTCHA challenge instead of search results."""


# ── Scraper ────────────────────────────────────────────────────────────── #

class IndianaMyCaseScraper:
    """
    Statewide Indiana eviction scraper.

    Usage::

        scraper = IndianaMyCaseScraper(lookback_days=7)
        filings = asyncio.run(scraper.scrape())
    """

    def __init__(
        self,
        lookback_days: int = 7,
        court_id: int = _STATEWIDE_COURT_ID,
        mode: str = "filings",
    ) -> None:
        self.lookback_days = lookback_days
        self.court_id = court_id
        self.mode = mode
        self._session: requests.Session | None = None
        self.last_error: str | None = None

    # ── Public interface ──────────────────────────────────────────────── #

    async def scrape(self) -> list[Filing]:
        """Async entry point — delegates sync HTTP work to a thread."""
        return await asyncio.to_thread(self._scrape_sync)

    # ── Core sync implementation ──────────────────────────────────────── #

    def _scrape_sync(self) -> list[Filing]:
        self.last_error = None
        if not self._init_session():
            return []

        today = court_today(COURT_TIMEZONE)
        start = today - timedelta(days=self.lookback_days)

        log.info(
            f"Indiana MyCase [{self.mode}]: "
            f"{start.strftime('%m/%d/%Y')} -> {today.strftime('%m/%d/%Y')}"
        )

        try:
            ev_cases = self._search_range(start, today)
        except _PortalBlockedError as exc:
            self.last_error = str(exc)
            log.error(f"Indiana MyCase: {exc}")
            return []
        log.info(f"Indiana MyCase: {len(ev_cases)} EV case(s) found")

        filings: list[Filing] = []
        for case in ev_cases:
            try:
                filing = self._fetch_detail(case)
                if filing:
                    filings.append(filing)
            except Exception as exc:
                log.warning(
                    f"Detail fetch failed for {case.get('CaseNumber', '?')}: {exc}"
                )
            time.sleep(random.uniform(_DETAIL_DELAY_MIN, _DETAIL_DELAY_MAX))

        log.info(f"Indiana MyCase: {len(filings)} filing(s) returned")
        return filings

    # ── Session management ────────────────────────────────────────────── #

    def _init_session(self) -> bool:
        """Create a new session and establish the portal cookie. Returns True on success."""
        try:
            self._session = self._new_session()
            resp = self._session.get(_BASE_URL, headers=_INIT_HEADERS, timeout=20)
            resp.raise_for_status()
            return True
        except Exception as exc:
            self.last_error = f"session init failed: {exc}"
            log.error(f"Indiana MyCase: {self.last_error}")
            return False

    def _refresh_session(self) -> bool:
        """Re-init the session after a 403. Returns True on success."""
        log.info("Indiana MyCase: session expired — refreshing")
        return self._init_session()

    # ── Search ────────────────────────────────────────────────────────── #

    def _search_range(self, start: date, end: date) -> list[dict]:
        """
        Search [start, end] for EV filings. If TotalResults hits the portal
        cap (1001), bisect the range and merge both halves.
        """
        start_str = start.strftime("%m/%d/%Y")
        end_str   = end.strftime("%m/%d/%Y")

        all_cases: list[dict] = []
        seen: set[str] = set()
        skip = 0

        while True:
            payload = {
                "Mode":        "ByParty",
                "Business":    "",
                "FileStart":   start_str,
                "FileEnd":     end_str,
                "Categories":  ["CV"],
                "Limits":      None,
                "CourtItemID": self.court_id,
                "ActiveFlag":  "All",
                "Advanced":    True,
                "SoundEx":     False,
                "Skip":        skip,
                "Take":        _PAGE_SIZE,
                "Sort":        "FileDate DESC",
            }

            try:
                resp = self._session.post(
                    _SEARCH_URL,
                    json=payload,
                    headers=_SEARCH_HEADERS,
                    timeout=30,
                )
                if resp.status_code == 403:
                    log.warning(
                        f"Search 403 at skip={skip} — waiting {_SEARCH_403_WAIT}s then retrying"
                    )
                    time.sleep(_SEARCH_403_WAIT)
                    self._refresh_session()
                    resp = self._session.post(
                        _SEARCH_URL,
                        json=payload,
                        headers=_SEARCH_HEADERS,
                        timeout=30,
                    )
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                log.warning(f"Search error skip={skip}: {exc}")
                break

            # The portal answers a blocked/throttled session with HTTP 200 and a
            # CaptchaKey payload instead of search results — not a 403/exception,
            # so it would otherwise look like a genuinely empty result set.
            if "TotalResults" not in data:
                raise _PortalBlockedError(
                    f"portal returned non-results payload (CAPTCHA challenge) "
                    f"at skip={skip}, range={start_str}->{end_str}: {data}"
                )

            total = data.get("TotalResults", 0)
            results = data.get("Results") or []

            # Portal caps at 1001 — bisect and recurse
            if total >= _RESULTS_CAP and skip == 0:
                log.info(
                    f"Indiana MyCase: hit results cap for {start_str}->{end_str}, "
                    f"bisecting date range"
                )
                if start != end:
                    mid   = start + (end - start) // 2
                    left  = self._search_range(start, mid)
                    right = self._search_range(mid + timedelta(days=1), end)
                    merged: list[dict] = []
                    merged_seen: set[str] = set()
                    for c in left + right:
                        cn = c.get("CaseNumber", "")
                        if cn and cn not in merged_seen:
                            merged_seen.add(cn)
                            merged.append(c)
                    return merged
                # Single-day window still capped — fall through and paginate

            for result in results:
                cn = result.get("CaseNumber", "")
                ct = result.get("CaseType", "")
                if ct == _EV_CASE_TYPE and cn and cn not in seen:
                    seen.add(cn)
                    all_cases.append(result)

            skip += _PAGE_SIZE
            if skip >= total or not results:
                break

            time.sleep(random.uniform(_SEARCH_DELAY_MIN, _SEARCH_DELAY_MAX))

        return all_cases

    # ── Detail fetch ──────────────────────────────────────────────────── #

    def _fetch_detail(self, summary: dict) -> Filing | None:
        case_number = summary.get("CaseNumber", "")
        case_token  = summary.get("CaseToken",  "")
        file_date   = summary.get("FileDate",   "")
        court_name  = summary.get("Court",      "")

        if not case_token or not case_number:
            log.warning(f"Skipping case with missing token/number: {summary}")
            return None

        ts_ms = int(time.time() * 1000)
        url   = f"{_SUMMARY_URL}?CaseToken={case_token}&SRCT=&_={ts_ms}"

        resp = self._session.get(url, headers=_DETAIL_HEADERS, timeout=20)

        # Session expired mid-run — refresh and retry once
        if resp.status_code == 403:
            if self._refresh_session():
                resp = self._session.get(url, headers=_DETAIL_HEADERS, timeout=20)

        resp.raise_for_status()
        detail = resp.json()

        parties = detail.get("Parties") or []
        events  = detail.get("Events")  or []

        defendant_name, defendant_addr = self._extract_defendant(parties)
        plaintiff_name                 = self._extract_plaintiff(parties)
        hearing_dt                     = self._first_hearing_date(events)

        if not defendant_name:
            log.warning(f"{case_number}: no defendant found — skipping")
            return None

        if self.mode == "judgments" and not self._has_judgment(events):
            return None

        filing_date = self._parse_date(file_date) if file_date else court_today(COURT_TIMEZONE)
        county      = self._extract_county(court_name)

        return Filing(
            case_number=case_number,
            tenant_name=clean_tenant_name(defendant_name) or defendant_name,
            property_address=defendant_addr or "Unknown",
            landlord_name=plaintiff_name or "Unknown",
            filing_date=filing_date,
            court_date=hearing_dt,
            state=STATE,
            county=county,
            notice_type=NOTICE_TYPE,
            source_url=f"{_BASE_URL}#/vw/CaseSummary/{case_token}",
            claim_amount=None,
        )

    # ── Party parsing ─────────────────────────────────────────────────── #

    @staticmethod
    def _extract_defendant(parties: list[dict]) -> tuple[str, str]:
        for party in parties:
            if party.get("Connection") == _DEFENDANT_CODE:
                name = (party.get("Name") or "").strip()
                addr = IndianaMyCaseScraper._format_address(party.get("Address") or {})
                return name, addr
        return "", ""

    @staticmethod
    def _extract_plaintiff(parties: list[dict]) -> str:
        for party in parties:
            if party.get("Connection") == _PLAINTIFF_CODE:
                return (party.get("Name") or "").strip()
        return ""

    @staticmethod
    def _format_address(addr: dict) -> str:
        if not addr:
            return ""
        line1 = (addr.get("Line1") or "").strip()
        city  = (addr.get("City")  or "").strip()
        state = (addr.get("State") or "").strip()
        zip_  = (addr.get("Zip")   or "").strip()

        if not line1:
            return ""

        parts = [line1]
        if city:
            parts.append(city)
        if state and zip_:
            parts.append(f"{state} {zip_}")
        elif state:
            parts.append(state)

        return ", ".join(parts)

    # ── Event parsing ─────────────────────────────────────────────────── #

    @staticmethod
    def _first_hearing_date(events: list[dict]) -> date | None:
        today = date.today()
        best: date | None = None
        for event in events:
            hearing = event.get("HearingEvent")
            if not hearing:
                continue
            sessions = hearing.get("Sessions") or []
            if not sessions:
                continue
            raw = sessions[0].get("SessionDate") or ""
            if not raw:
                continue
            try:
                dt = IndianaMyCaseScraper._parse_date(raw)
                if dt >= today and (best is None or dt < best):
                    best = dt
            except ValueError:
                continue
        return best

    @staticmethod
    def _has_judgment(events: list[dict]) -> bool:
        """
        ISTS mode stub — checks for common judgment description keywords.
        Update once a real closed EV case is inspected.
        """
        _KEYWORDS = {"judgment", "default judgment", "order", "final judgment"}
        for event in events:
            desc = (event.get("Description") or "").lower()
            if any(kw in desc for kw in _KEYWORDS):
                return True
        return False

    # ── Helpers ───────────────────────────────────────────────────────── #

    # Indiana court names: "Lake Superior Court, Division 4", "Marion County",
    # "Tippecanoe Superior Court 7", "Pike Township", etc.
    # Strip everything from the first "Superior/Circuit/County/Township" keyword onwards.
    _COURT_SUFFIX_RE = re.compile(
        r"\s+(?:Superior|Circuit|County|Township)\b.*$",
        re.IGNORECASE,
    )

    @staticmethod
    def _extract_county(court_name: str) -> str:
        cleaned = IndianaMyCaseScraper._COURT_SUFFIX_RE.sub("", court_name).strip()
        return cleaned or court_name

    @staticmethod
    def _parse_date(raw: str) -> date:
        raw = raw.split("T")[0].strip()
        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
        raise ValueError(f"Cannot parse date: {raw!r}")

    @staticmethod
    def _new_session() -> requests.Session:
        s = requests.Session()
        s.headers.update({
            "User-Agent":      _USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
        })
        return s
