from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta

from models.filing import Filing
from scrapers.base_scraper import BaseScraper
from scrapers.dates import court_today

log = logging.getLogger(__name__)

PORTAL_URL = "https://public.courts.in.gov/mycase/"
CASE_DETAIL_URL = "https://public.courts.in.gov/mycase/Case/CaseSummary"

STATE = "IN"
COUNTY = "Marion"
MARION_COUNTY_CODE = "49"
COURT_TIMEZONE = "America/Indiana/Indianapolis"

# Iterate A-Z + 0-9 to cover all party last names (incl. corporate names)
_PREFIXES = list("abcdefghijklmnopqrstuvwxyz0123456789")

# Injected into page context — has session cookies, avoids CORS
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
    } catch(e) {
        return {_error: e.toString()};
    }
}
"""

# Extract party + event data from KnockoutJS viewmodel; falls back to DOM text
_JS_DETAIL = """
() => {
    try {
        const el = document.querySelector('[data-bind]');
        if (el && typeof ko !== 'undefined') {
            const ctx = ko.contextFor(el);
            const root = ctx && (ctx.$root || ctx.$data);
            const ob = root && root.ob;
            if (ob) {
                return {
                    parties: ko.toJS(ob.Parties || []),
                    events:  ko.toJS(ob.Events  || []),
                };
            }
        }
    } catch(e) {}
    return {parties: [], events: [], domText: document.body.innerText};
}
"""


class MarionCountyScraper(BaseScraper):
    """
    Scrapes Indiana public.courts.in.gov/mycase for Marion County eviction (EV)
    filings. Uses the SearchCases JSON API (called from browser context to reuse
    the established session) and KnockoutJS viewmodel extraction for case detail.
    """

    def __init__(self, headless: bool = True, lookback_days: int = 7):
        super().__init__(headless=headless)
        self.lookback_days = lookback_days

    async def scrape(self) -> list[Filing]:
        page = await self._launch_browser()
        filings: list[Filing] = []

        today = court_today(COURT_TIMEZONE)
        start = today - timedelta(days=self.lookback_days)
        start_str = start.strftime("%m/%d/%Y")
        end_str = today.strftime("%m/%d/%Y")

        try:
            log.info(f"Marion County IN: {start_str} → {end_str}")
            await page.goto(PORTAL_URL, wait_until="load", timeout=60_000)
            await page.wait_for_timeout(3000)

            seen: set[str] = set()
            ev_cases: list[dict] = []

            for prefix in _PREFIXES:
                payload = {
                    "Mode": "ByParty",
                    "Last": prefix,
                    "First": "",
                    "Middle": "",
                    "CourtItemID": None,
                    "Categories": None,
                    "Limits": None,
                    "ActiveFlag": "All",
                    "FileStart": start_str,
                    "FileEnd": end_str,
                    "CountyCode": "49",
                    "Advanced": True,
                    "SoundEx": False,
                    "NewSearch": True,
                    "CaptchaAnswer": None,
                    "Skip": 0,
                    "Take": 200,
                    "Sort": "FileDate DESC",
                }

                try:
                    data = await page.evaluate(_JS_SEARCH, payload)
                except Exception as e:
                    log.warning(f"Search API error prefix={prefix!r}: {e}")
                    continue

                if not isinstance(data, dict):
                    continue
                if "_error" in data:
                    log.warning(f"Search fetch error prefix={prefix!r}: {data['_error']}")
                    continue
                if "CaptchaKey" in data:
                    log.warning(f"CAPTCHA triggered at prefix={prefix!r} — skipping")
                    continue

                for result in (data.get("Results") or []):
                    cn = result.get("CaseNumber", "")
                    ct = result.get("CaseType", "")
                    if "EV" in ct.upper() and cn and cn not in seen:
                        seen.add(cn)
                        ev_cases.append(result)

                await asyncio.sleep(0.4)

            log.info(f"Marion County: {len(ev_cases)} unique EV cases")

            for summary in ev_cases:
                try:
                    filing = await self._fetch_detail(page, summary)
                    if filing:
                        filings.append(filing)
                    await asyncio.sleep(0.6)
                except Exception as e:
                    log.warning(f"Detail failed {summary.get('CaseNumber', '?')}: {e}")

        except Exception as e:
            log.error(f"Marion County scrape failed: {e}", exc_info=True)
        finally:
            await self._close_browser()

        log.info(f"Marion County returned {len(filings)} filings")
        return filings

    async def _fetch_detail(self, page, summary: dict) -> Filing | None:
        case_number = summary.get("CaseNumber", "")
        case_token = summary.get("CaseToken", "")
        file_date_raw = summary.get("FileDate", "")
        style = summary.get("Style") or summary.get("CaseStyle") or ""

        if not case_token or not case_number:
            return None

        detail_url = f"{CASE_DETAIL_URL}?CaseToken={case_token}"
        await page.goto(detail_url, wait_until="load", timeout=60_000)
        await page.wait_for_timeout(2000)

        detail = await page.evaluate(_JS_DETAIL)
        parties = detail.get("parties", [])
        events = detail.get("events", [])

        plaintiff, defendant, def_address = self._parse_parties(parties, style)

        court_date = self._first_event_date(events)
        filing_date = self._parse_date(file_date_raw) if file_date_raw else court_today(COURT_TIMEZONE)

        if not defendant:
            log.warning(f"No defendant found for {case_number}")
            return None

        return Filing(
            case_number=case_number,
            tenant_name=defendant,
            property_address=def_address or "Unknown",
            landlord_name=plaintiff or "Unknown",
            filing_date=filing_date,
            court_date=court_date,
            state=STATE,
            county=COUNTY,
            notice_type="Eviction",
            source_url=detail_url,
        )

    @staticmethod
    def _parse_parties(
        parties: list[dict], style: str
    ) -> tuple[str, str, str]:
        plaintiff = defendant = def_address = ""

        for party in parties:
            role = (
                party.get("ConnectionType") or party.get("PartyType") or ""
            ).lower()
            name = (
                party.get("ExtendedName")
                or party.get("NameFMLS")
                or party.get("Name")
                or ""
            ).strip()
            addr_obj = party.get("Address") or {}

            if "plaintiff" in role and not plaintiff:
                plaintiff = name
            elif "defendant" in role and not defendant:
                defendant = name
                if not addr_obj.get("Masked"):
                    def_address = MarionCountyScraper._format_address(addr_obj)

        # Fallback: parse "Plaintiff v. Defendant" style string
        if (not plaintiff or not defendant) and " v. " in style:
            parts = style.split(" v. ", 1)
            plaintiff = plaintiff or parts[0].strip()
            defendant = defendant or parts[1].strip()

        return plaintiff, defendant, def_address

    @staticmethod
    def _format_address(addr: dict) -> str:
        parts = [
            addr.get(k, "").strip()
            for k in ("Line1", "Line2", "Line3")
            if addr.get(k, "").strip()
        ]
        city = addr.get("City", "").strip()
        state = addr.get("State", "").strip()
        zip_ = addr.get("Zip", "").strip()
        if city:
            parts.append(city)
        if state and zip_:
            parts.append(f"{state} {zip_}")
        elif state:
            parts.append(state)
        return ", ".join(parts)

    @staticmethod
    def _first_event_date(events: list[dict]) -> date | None:
        for event in events:
            raw = event.get("AppearByDate") or event.get("EventDate") or ""
            if raw:
                try:
                    return MarionCountyScraper._parse_date(raw)
                except ValueError:
                    continue
        return None

    @staticmethod
    def _parse_date(raw: str) -> date:
        raw = raw.split("T")[0].strip()
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
        raise ValueError(f"Cannot parse date: {raw!r}")
