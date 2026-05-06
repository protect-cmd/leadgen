from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, timedelta

from models.filing import Filing
from scrapers.base_scraper import BaseScraper
from scrapers.dates import court_today

log = logging.getLogger(__name__)

PORTAL_URL = "https://researchga.tylerhost.net/CourtRecordsSearch/ui/home"
LOGIN_URL = "https://researchga.tylerhost.net/CourtRecordsSearch/ui/login"
CASE_DETAIL_BASE = "https://researchga.tylerhost.net/CourtRecordsSearch/ui/case"

STATE = "GA"
COURT_TIMEZONE = "America/New_York"

# Posted from browser context to reuse session cookies (auth required)
_JS_SEARCH = """
async (payload) => {
    try {
        const tz = new Date().getTimezoneOffset();
        const r = await fetch(`/CourtRecordsSearch/search?timeZoneOffsetInMinutes=${tz}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'application/json, text/plain, */*',
                'X-Requested-With': 'XMLHttpRequest',
            },
            body: JSON.stringify(payload),
            credentials: 'same-origin',
        });
        if (!r.ok) return {_error: `HTTP ${r.status}`};
        return await r.json();
    } catch(e) {
        return {_error: e.toString()};
    }
}
"""

# Tries to find a street address in the case detail page HTML
_JS_EXTRACT_ADDRESS = """
() => {
    try {
        const text = document.body.innerText;
        const match = text.match(/\\d+\\s+\\w[\\w .]+(?:St(?:reet)?|Ave(?:nue)?|Rd|Road|Dr(?:ive)?|Blvd|Boulevard|Ln|Lane|Ct|Court|Way|Pl(?:ace)?|Pkwy|Parkway)[^\\n]{0,60}/i);
        if (match) return match[0].trim();
    } catch(e) {}
    return null;
}
"""


class ReSearchGAScraper(BaseScraper):
    """
    Scrapes re:SearchGA (Tyler Technologies Odyssey) for Georgia
    Dispossessory/Distress filings. Requires a free re:SearchGA account
    (RESEARCHGA_EMAIL / RESEARCHGA_PASSWORD in env). Free tier allows
    30 searches/day, 150/month — one search per page of results.

    Currently only Clayton County feeds dispossessory cases into re:SearchGA;
    additional counties will appear automatically once they integrate.
    """

    def __init__(self, headless: bool = True, lookback_days: int = 7):
        super().__init__(headless=headless)
        self.lookback_days = lookback_days

    async def scrape(self) -> list[Filing]:
        page = await self._launch_browser()
        filings: list[Filing] = []

        today = court_today(COURT_TIMEZONE)
        cutoff = today - timedelta(days=self.lookback_days)

        try:
            await self._login(page)
            log.info(f"re:SearchGA GA: {cutoff} → {today}")

            cases = await self._fetch_cases(page, cutoff)
            log.info(f"re:SearchGA: {len(cases)} dispossessory cases in window")

            for hit in cases:
                try:
                    filing = await self._build_filing(page, hit)
                    if filing:
                        filings.append(filing)
                    await asyncio.sleep(0.5)
                except Exception as e:
                    log.warning(f"Detail failed {hit.get('caseNumber', '?')}: {e}")

        except Exception as e:
            log.error(f"re:SearchGA scrape failed: {e}", exc_info=True)
        finally:
            await self._close_browser()

        log.info(f"re:SearchGA returned {len(filings)} filings")
        return filings

    async def _login(self, page) -> None:
        email = os.environ.get("RESEARCHGA_EMAIL", "")
        password = os.environ.get("RESEARCHGA_PASSWORD", "")

        if not email or not password:
            log.warning("RESEARCHGA_EMAIL/PASSWORD not set — navigating unauthenticated")
            await page.goto(PORTAL_URL, wait_until="load", timeout=60_000)
            return

        await page.goto(LOGIN_URL, wait_until="load", timeout=60_000)
        await page.wait_for_timeout(2000)

        try:
            await page.fill('input[type="email"]', email)
            await page.fill('input[type="password"]', password)
            await page.click('button[type="submit"]')
            await page.wait_for_timeout(3000)
            log.info("re:SearchGA: login submitted")
        except Exception as e:
            log.warning(f"re:SearchGA login step failed: {e}")

    async def _fetch_cases(self, page, cutoff: date) -> list[dict]:
        payload = {
            "searchIndexType": "Cases",
            "advancedSearchConditions": {
                "advancedSearchConditions": [
                    {
                        "conditionOperation": 0,
                        "fieldOption": 5,
                        "value": None,
                        "fromValue": None,
                        "toValue": None,
                        "firstName": None,
                        "dob": None,
                        "dlNumber": None,
                        "nickname": None,
                        "lastName": None,
                        "valueSet": ["dispossessory/distress"],
                    }
                ]
            },
            "pageIndex": 0,
            "pageSize": 200,
            "queryString": None,
            "sortFieldOrder": "desc",
            "sortFields": "0",
        }

        results: list[dict] = []
        page_index = 0

        while True:
            payload["pageIndex"] = page_index
            data = await page.evaluate(_JS_SEARCH, payload)

            if not isinstance(data, dict):
                log.warning("Unexpected search response type")
                break
            if "_error" in data:
                log.warning(f"Search API error: {data['_error']}")
                break

            search_results = (data.get("result") or {}).get("searchResults") or {}
            hits = search_results.get("hits") or []

            if not hits:
                break

            past_window = False
            for hit in hits:
                filed_date = self._parse_date_str(hit.get("dateFiled", ""))
                if filed_date is None or filed_date < cutoff:
                    past_window = True
                    break
                results.append(hit)

            if past_window:
                break

            actual_total = search_results.get("actualTotal", 0)
            if (page_index + 1) * 200 >= actual_total:
                break

            page_index += 1
            await asyncio.sleep(0.5)

        return results

    async def _build_filing(self, page, hit: dict) -> Filing | None:
        case_number = hit.get("caseNumber", "")
        case_data_id = hit.get("caseDataID", "")

        if not case_number:
            return None

        parties = hit.get("parties") or []
        plaintiff = defendant = ""
        for party in parties:
            role = (party.get("partyTypeCode") or "").lower()
            name = (party.get("name") or "").strip()
            if "plaintiff" in role and not plaintiff:
                plaintiff = name
            elif "defendant" in role and not defendant:
                defendant = name

        # Fallback: parse "Plaintiff vs. Defendant" from description
        description = hit.get("description", "")
        if (not plaintiff or not defendant) and " vs. " in description:
            parts = description.split(" vs. ", 1)
            plaintiff = plaintiff or parts[0].strip()
            defendant = defendant or parts[1].strip()

        if not defendant:
            log.warning(f"No defendant for {case_number}")
            return None

        detail_url = f"{CASE_DETAIL_BASE}/{case_data_id}" if case_data_id else ""
        property_address = "Unknown"

        if detail_url:
            try:
                await page.goto(detail_url, wait_until="load", timeout=60_000)
                await page.wait_for_timeout(2000)
                addr = await page.evaluate(_JS_EXTRACT_ADDRESS)
                if addr:
                    property_address = addr
            except Exception as e:
                log.warning(f"Case detail failed {case_number}: {e}")

        jurisdiction = hit.get("jurisdiction", "")
        county = jurisdiction.split(" ")[0] if jurisdiction else "Clayton"

        filing_date = self._parse_date_str(hit.get("dateFiled", "")) or court_today(COURT_TIMEZONE)

        return Filing(
            case_number=case_number,
            tenant_name=defendant,
            property_address=property_address,
            landlord_name=plaintiff or "Unknown",
            filing_date=filing_date,
            court_date=None,
            state=STATE,
            county=county,
            notice_type="Dispossessory",
            source_url=detail_url,
        )

    @staticmethod
    def _parse_date_str(raw: str) -> date | None:
        if not raw:
            return None
        raw = raw.split("T")[0].strip()
        for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
        return None
