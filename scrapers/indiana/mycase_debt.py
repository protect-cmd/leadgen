from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta

from models.debt_suit import DebtSuit
from scrapers.base_scraper import BaseScraper
from scrapers.dates import court_today
from services.name_utils import clean_tenant_name, parse_name

log = logging.getLogger(__name__)

PORTAL_URL = "https://public.courts.in.gov/mycase/"
# Detail data endpoint (returns JSON). The old KnockoutJS viewmodel scrape is
# dead after the SPA rewrite — this endpoint is the supported path.
DETAIL_URL = "https://public.courts.in.gov/mycase/Case/CaseSummary"

STATE = "IN"
COURT_TIMEZONE = "America/Indiana/Indianapolis"

# Cosner Drake target = debt-collection lawsuits just filed. CC (Civil
# Collection) is the dominant civil-debt type; SC (Small Claims) is mixed and
# opt-in. EV (evictions) is deliberately excluded — that feeds VDG, not CD.
DEFAULT_CASE_TYPES: tuple[str, ...] = ("CC",)

# MyCase party "Connection" codes (confirmed via live detail JSON).
CONNECTION_DEFENDANT = 2   # the sued consumer — the lead
CONNECTION_PLAINTIFF = 3   # the creditor — NEVER the target

# Top-population counties → MyCase CountyCode (2-digit, alphabetical Indiana
# county index; Marion=49 confirmed live). The smoke run validates the rest.
DEFAULT_COUNTIES: dict[str, str] = {
    "Marion": "49",       # Indianapolis
    "Lake": "45",         # Gary / Hammond
    "Allen": "02",        # Fort Wayne
    "Hamilton": "29",     # Carmel / Fishers
    "St. Joseph": "71",   # South Bend
    "Vanderburgh": "82",  # Evansville
}

_PAGE_SIZE = 50
_MAX_PAGES = 25  # hard stop; TotalResults caps ~1000 => 20 pages of 50

# Injected into page context so the fetch reuses session cookies (avoids CORS).
_JS_SEARCH = """
async (payload) => {
    try {
        const r = await fetch('/mycase/Search/SearchCases', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-Requested-With': 'XMLHttpRequest',
                'Accept': 'application/json, text/javascript, */*; q=0.01',
            },
            body: JSON.stringify(payload),
            credentials: 'same-origin',
        });
        return await r.json();
    } catch (e) {
        return {_error: e.toString()};
    }
}
"""

_JS_DETAIL = """
async (token) => {
    try {
        const r = await fetch(
            '/mycase/Case/CaseSummary?CaseToken=' + encodeURIComponent(token),
            {
                headers: {
                    'X-Requested-With': 'XMLHttpRequest',
                    'Accept': 'application/json',
                },
                credentials: 'same-origin',
            });
        return await r.json();
    } catch (e) {
        return {_error: e.toString()};
    }
}
"""


class IndianaMyCaseDebtScraper(BaseScraper):
    """Scrapes Indiana statewide MyCase (public.courts.in.gov/mycase) for
    debt-collection lawsuits (CC - Civil Collection) just filed, for Cosner
    Drake. Enumerates by filing date with NO party name (blank Last + date
    range + CountyCode, paginated), then pulls each case detail for the
    defendant's home address.
    """

    def __init__(
        self,
        headless: bool = True,
        lookback_days: int = 7,
        counties: dict[str, str] | None = None,
        case_types: tuple[str, ...] = DEFAULT_CASE_TYPES,
        max_cases: int | None = None,
    ):
        super().__init__(headless=headless)
        self.lookback_days = lookback_days
        self.counties = counties or DEFAULT_COUNTIES
        self.case_types = tuple(ct.upper() for ct in case_types)
        self.max_cases = max_cases
        self.last_error: str | None = None
        # Populated during scrape() for smoke reporting.
        self.stats: dict[str, int] = {}

    async def scrape(self) -> list[DebtSuit]:
        page = await self._launch_browser()
        suits: list[DebtSuit] = []

        today = court_today(COURT_TIMEZONE)
        start = today - timedelta(days=self.lookback_days)
        start_str = start.strftime("%m/%d/%Y")
        end_str = today.strftime("%m/%d/%Y")

        seen: set[str] = set()
        debt_cases: list[dict] = []
        per_county: dict[str, int] = {}

        try:
            log.info(f"Indiana MyCase debt: {start_str} -> {end_str} "
                     f"types={self.case_types} counties={list(self.counties)}")
            await page.goto(PORTAL_URL, wait_until="load", timeout=60_000)
            await page.wait_for_timeout(3000)

            for county, code in self.counties.items():
                county_hits = await self._enumerate_county(
                    page, code, start_str, end_str, seen, debt_cases
                )
                per_county[county] = county_hits
                log.info(f"{county} ({code}): {county_hits} debt cases")

            log.info(f"Total unique debt cases: {len(debt_cases)}")

            detail_targets = debt_cases
            if self.max_cases is not None:
                detail_targets = debt_cases[: self.max_cases]

            masked = 0
            no_address = 0
            for summary in detail_targets:
                try:
                    suit = await self._fetch_detail(page, summary)
                    if suit is None:
                        no_address += 1
                    elif suit == "masked":
                        masked += 1
                    else:
                        suits.append(suit)
                    await asyncio.sleep(0.5)
                except Exception as e:
                    log.warning(f"Detail failed {summary.get('CaseNumber', '?')}: {e}")
                    no_address += 1

            self.stats = {
                "debt_cases": len(debt_cases),
                "detail_fetched": len(detail_targets),
                "with_address": len(suits),
                "masked": masked,
                "no_address": no_address,
                **{f"county_{c}": n for c, n in per_county.items()},
            }

        except Exception as e:
            self.last_error = str(e)
            log.error(f"Indiana MyCase debt scrape failed: {e}", exc_info=True)
        finally:
            await self._close_browser()

        log.info(f"Indiana MyCase debt returned {len(suits)} suits")
        return suits

    async def _enumerate_county(
        self,
        page,
        county_code: str,
        start_str: str,
        end_str: str,
        seen: set[str],
        out: list[dict],
    ) -> int:
        """Paginate one county's filing-date window, collecting on-target debt
        cases. Returns the count added for this county."""
        added = 0
        for page_idx in range(_MAX_PAGES):
            payload = {
                "Mode": "ByParty",
                "Last": "", "First": "", "Middle": "",
                "CourtItemID": None, "Categories": None, "Limits": None,
                "ActiveFlag": "All",
                "FileStart": start_str, "FileEnd": end_str,
                "CountyCode": county_code,
                "Advanced": True, "SoundEx": False,
                "NewSearch": page_idx == 0,
                "CaptchaAnswer": None,
                "Skip": page_idx * _PAGE_SIZE, "Take": _PAGE_SIZE,
                "Sort": "FileDate DESC",
            }
            try:
                data = await page.evaluate(_JS_SEARCH, payload)
            except Exception as e:
                log.warning(f"Search error county={county_code} skip={payload['Skip']}: {e}")
                break

            if not isinstance(data, dict) or "_error" in data:
                log.warning(f"Search bad response county={county_code}: {data}")
                break
            if "CaptchaKey" in data:
                log.warning(f"CAPTCHA triggered county={county_code} — stopping county")
                self.last_error = "captcha"
                break

            results = data.get("Results") or []
            total = data.get("TotalResults") or 0
            for r in results:
                if not self._is_target_type(r.get("CaseType", "")):
                    continue
                cn = r.get("CaseNumber", "")
                if cn and cn not in seen:
                    seen.add(cn)
                    out.append(r)
                    added += 1

            fetched = payload["Skip"] + len(results)
            if not results or fetched >= total:
                break
            await asyncio.sleep(0.4)
        return added

    def _is_target_type(self, case_type: str) -> bool:
        code = (case_type or "").strip()[:2].upper()
        return code in self.case_types

    async def _fetch_detail(self, page, summary: dict):
        """Return a DebtSuit, the sentinel string "masked", or None (no address)."""
        case_number = summary.get("CaseNumber", "")
        token = summary.get("CaseToken", "")
        if not token or not case_number:
            return None

        detail = await page.evaluate(_JS_DETAIL, token)
        if not isinstance(detail, dict) or "_error" in detail:
            log.warning(f"Detail bad response {case_number}: {detail}")
            return None
        return self._suit_from_detail(detail, summary, token)

    # ---- pure helpers (unit-testable without a browser) ----

    def _suit_from_detail(self, detail: dict, summary: dict, token: str):
        """Build a DebtSuit from a Case/CaseSummary JSON payload. Returns the
        sentinel "masked" when the defendant address is sealed, or None when no
        usable defendant/address is present. Network-free for testability."""
        case_number = detail.get("CaseNumber") or summary.get("CaseNumber", "")
        parties = detail.get("Parties") or []
        defendant = self._first_party(parties, CONNECTION_DEFENDANT)
        plaintiff = self._first_party(parties, CONNECTION_PLAINTIFF)

        if not defendant:
            return None

        addr_obj = defendant.get("Address") or {}
        if addr_obj.get("Masked"):
            return "masked"
        address = self._format_address(addr_obj)
        if not address:
            return None

        defendant_name = self._normalize_name(defendant.get("Name") or "")
        if not defendant_name:
            return None

        plaintiff_name = (plaintiff.get("Name") if plaintiff else None) or summary.get("Style")

        return DebtSuit(
            case_number=case_number,
            defendant_name=defendant_name,
            defendant_address=address,
            plaintiff_name=plaintiff_name,
            filing_date=self._parse_date(detail.get("FileDate") or summary.get("FileDate")),
            case_type_code=(detail.get("CaseTypeCode") or summary.get("CaseType", "")[:2]).upper(),
            county=self._county_name_for(detail.get("CountyCode") or summary.get("CountyCode")),
            state=STATE,
            court_code=(detail.get("CourtCode") or summary.get("CourtCode") or "").strip() or None,
            amount=None,
            amount_kind=None,
            case_status=detail.get("CaseStatus") or summary.get("CaseStatus"),
            source_url=f"{DETAIL_URL}?CaseToken={token}",
        )

    @staticmethod
    def _first_party(parties: list[dict], connection: int) -> dict | None:
        for p in parties:
            if p.get("Connection") == connection:
                return p
        return None

    @staticmethod
    def _format_address(addr: dict) -> str:
        parts = [
            (addr.get(k) or "").strip()
            for k in ("Line1", "Line2", "Line3", "Line4")
            if (addr.get(k) or "").strip()
        ]
        city = (addr.get("City") or "").strip()
        state = (addr.get("State") or "").strip()
        zip_ = (addr.get("Zip") or "").strip()
        if not parts or not zip_:
            return ""  # must have a street line + ZIP to be pipeline-usable
        if city:
            parts.append(city)
        if state and zip_:
            parts.append(f"{state} {zip_}")
        elif state:
            parts.append(state)
        return ", ".join(parts)

    @staticmethod
    def _normalize_name(raw: str) -> str:
        """MyCase gives 'LAST, FIRST [MIDDLE]'. Return 'First Last' title-cased,
        or '' for placeholder/business names."""
        cleaned = clean_tenant_name(raw)
        if not cleaned:
            return ""
        first, last = parse_name(cleaned)
        if first and last:
            return f"{first} {last}".title()
        return cleaned.title()

    def _county_name_for(self, county_code: str | None) -> str:
        if county_code:
            for name, code in self.counties.items():
                if code == county_code:
                    return name
        return ""

    @staticmethod
    def _parse_date(raw: str | None) -> date | None:
        if not raw:
            return None
        raw = raw.split("T")[0].strip()
        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
        return None
