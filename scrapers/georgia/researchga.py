from __future__ import annotations

import asyncio
import logging
import os
import re
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
DEFAULT_HEARING_LOOKAHEAD_DAYS = 45
DEFAULT_SEARCH_WINDOW_DAYS = 7
DEFAULT_PAGE_SIZE = 200
MAX_SEARCH_ROWS = 5000
TYLER_EXPORT_ROW_CAP = 1000

DISPOSSESSORY_CASE_TYPES = [
    "application for dispossessory",
    "civil dispossessory",
    "dispossessory",
    "dispossessory - distress warrant",
    "dispossessory - distress warrant - efiled",
    "dispossessory - possession & money judgment",
    "dispossessory - possession & money judgment - efiled",
    "dispossessory - possession only",
    "dispossessory - possession only - efiled",
    "dispossessory / distress",
]

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


class ReSearchGAScraper(BaseScraper):
    """
    Scrapes re:SearchGA (Tyler Technologies Odyssey) for Georgia Magistrate
    Court dispossessory hearings. Requires a re:SearchGA account
    (RESEARCHGA_EMAIL / RESEARCHGA_PASSWORD in env).

    The source does not expose property addresses in hearing search/export data.
    Keep property_address="Unknown" so downstream dedupe and enrichment can make
    the volume/quality decision instead of dropping otherwise valid filings.
    """

    def __init__(
        self,
        headless: bool = True,
        lookback_days: int = 7,
        hearing_lookahead_days: int = DEFAULT_HEARING_LOOKAHEAD_DAYS,
    ):
        super().__init__(headless=headless)
        self.lookback_days = lookback_days
        self.hearing_lookahead_days = hearing_lookahead_days

    async def scrape(self) -> list[Filing]:
        page = await self._launch_browser()
        today = court_today(COURT_TIMEZONE)
        search_start = today - timedelta(days=self.lookback_days)
        search_end = today + timedelta(days=self.hearing_lookahead_days)

        try:
            await self._login(page)
            log.info("re:SearchGA GA hearings: %s -> %s", search_start, search_end)

            hearings = await self._fetch_hearings(page, search_start, search_end)
            log.info("re:SearchGA: %s dispossessory hearing rows in window", len(hearings))
            filings = self._build_filings_from_hearings(hearings)

        except Exception as e:
            log.error("re:SearchGA scrape failed: %s", e, exc_info=True)
            filings = []
        finally:
            await self._close_browser()

        log.info("re:SearchGA returned %s filings", len(filings))
        return filings

    async def _login(self, page) -> None:
        email = os.environ.get("RESEARCHGA_EMAIL", "")
        password = os.environ.get("RESEARCHGA_PASSWORD", "")

        if not email or not password:
            log.warning("RESEARCHGA_EMAIL/PASSWORD not set; navigating unauthenticated")
            await page.goto(PORTAL_URL, wait_until="load", timeout=60_000)
            return

        await page.goto(PORTAL_URL, wait_until="load", timeout=60_000)
        await page.wait_for_timeout(2000)

        try:
            await page.click("text=Sign in with Your eFileGA Account")
            await page.wait_for_timeout(3000)
            await page.fill("#UserName", email)
            await page.fill("#Password", password)
            await page.click("button:has-text('Sign In')")
            await page.wait_for_timeout(5000)
            log.info("re:SearchGA: login submitted")
        except Exception as e:
            log.warning("re:SearchGA login step failed: %s", e)

    async def _fetch_hearings(
        self,
        page,
        from_date: date,
        to_date: date,
        *,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> list[dict]:
        results: list[dict] = []
        for window_start, window_end in self._date_windows(
            from_date=from_date,
            to_date=to_date,
            window_days=DEFAULT_SEARCH_WINDOW_DAYS,
        ):
            window_results = await self._fetch_hearings_window(
                page,
                window_start,
                window_end,
                page_size=page_size,
            )
            if len(window_results) >= TYLER_EXPORT_ROW_CAP and window_start < window_end:
                log.warning(
                    "re:SearchGA %s -> %s returned %s rows; splitting to daily windows",
                    window_start,
                    window_end,
                    len(window_results),
                )
                for day_start, day_end in self._date_windows(
                    from_date=window_start,
                    to_date=window_end,
                    window_days=1,
                ):
                    results.extend(
                        await self._fetch_hearings_window(
                            page,
                            day_start,
                            day_end,
                            page_size=page_size,
                        )
                    )
            else:
                results.extend(window_results)

            if len(results) >= MAX_SEARCH_ROWS:
                log.warning(
                    "re:SearchGA hit the %s-row safety cap; narrow the date range for a fuller backfill",
                    MAX_SEARCH_ROWS,
                )
                break

        return results[:MAX_SEARCH_ROWS]

    async def _fetch_hearings_window(
        self,
        page,
        from_date: date,
        to_date: date,
        *,
        page_size: int,
    ) -> list[dict]:
        results: list[dict] = []
        page_index = 0

        while len(results) < TYLER_EXPORT_ROW_CAP:
            payload = self._build_hearings_payload(
                from_date=from_date,
                to_date=to_date,
                page_index=page_index,
                page_size=page_size,
            )
            data = await page.evaluate(_JS_SEARCH, payload)

            if not isinstance(data, dict):
                log.warning("Unexpected re:SearchGA search response type")
                break
            if "_error" in data:
                log.warning("re:SearchGA search API error: %s", data["_error"])
                break

            search_results = self._extract_search_results(data)
            hits = search_results.get("hits") or []
            if not hits:
                break

            results.extend(hits)

            actual_total = search_results.get("actualTotal") or search_results.get("total") or 0
            if not isinstance(actual_total, int):
                actual_total = 0
            if (page_index + 1) * page_size >= actual_total:
                break

            page_index += 1
            await asyncio.sleep(0.5)

        if len(results) >= TYLER_EXPORT_ROW_CAP:
            log.warning(
                "re:SearchGA %s -> %s reached the %s-row Tyler window cap",
                from_date,
                to_date,
                TYLER_EXPORT_ROW_CAP,
            )

        return results[:TYLER_EXPORT_ROW_CAP]

    @staticmethod
    def _date_windows(
        *,
        from_date: date,
        to_date: date,
        window_days: int,
    ) -> list[tuple[date, date]]:
        if window_days < 1:
            raise ValueError("window_days must be at least 1")

        windows: list[tuple[date, date]] = []
        current = from_date
        while current <= to_date:
            window_end = min(current + timedelta(days=window_days - 1), to_date)
            windows.append((current, window_end))
            current = window_end + timedelta(days=1)
        return windows

    @staticmethod
    def _build_hearings_payload(
        *,
        from_date: date,
        to_date: date,
        page_index: int,
        page_size: int,
    ) -> dict:
        return {
            "searchIndexType": "Hearings",
            "advancedSearchConditions": {
                "advancedSearchConditions": [
                    {
                        "conditionOperation": 0,
                        "fieldOption": 10,
                        "value": None,
                        "fromValue": None,
                        "toValue": None,
                        "firstName": None,
                        "dob": None,
                        "dlNumber": None,
                        "nickname": None,
                        "lastName": None,
                        "valueSet": DISPOSSESSORY_CASE_TYPES,
                    },
                    {
                        "conditionOperation": 0,
                        "fieldOption": 0,
                        "value": None,
                        "fromValue": from_date.strftime("%m/%d/%Y"),
                        "toValue": to_date.strftime("%m/%d/%Y"),
                        "firstName": None,
                        "dob": None,
                        "dlNumber": None,
                        "nickname": None,
                        "lastName": None,
                        "valueSet": [],
                    },
                ]
            },
            "pageIndex": page_index,
            "pageSize": page_size,
            "queryString": None,
            "sortFieldOrder": "desc",
            "sortFields": "0",
        }

    @staticmethod
    def _extract_search_results(data: dict) -> dict:
        result = data.get("result") if isinstance(data.get("result"), dict) else data
        search_results = result.get("searchResults") if isinstance(result, dict) else None
        if isinstance(search_results, dict):
            return search_results
        if isinstance(result, dict) and isinstance(result.get("hits"), list):
            return result
        return {}

    @classmethod
    def _build_filings_from_hearings(cls, rows: list[dict]) -> list[Filing]:
        filings_by_case: dict[str, Filing] = {}
        for row in rows:
            filing = cls._build_filing_from_hearing(row)
            if not filing:
                continue
            existing = filings_by_case.get(filing.case_number)
            if existing is None or cls._prefer_filing(filing, existing):
                filings_by_case[filing.case_number] = filing
        return list(filings_by_case.values())

    @classmethod
    def _build_filing_from_hearing(cls, row: dict) -> Filing | None:
        case_number = cls._first_value(row, "Case Number", "caseNumber", "case_number")
        if not case_number:
            return None

        description = cls._first_value(
            row,
            "Case Description",
            "caseDescription",
            "description",
            "caseName",
        )
        landlord_name, tenant_name = cls._split_case_description(description)
        if not tenant_name:
            landlord_name, tenant_name = cls._party_names_from_hit(row)
        if not tenant_name:
            return None

        case_location = cls._first_value(
            row,
            "Case Location",
            "caseLocation",
            "caseJurisdiction",
            "jurisdiction",
        )
        case_type = cls._first_value(
            row,
            "Case Type",
            "caseType",
            "caseTypeCode",
        ) or "Dispossessory"
        hearing_type = cls._first_value(row, "Hearing Type", "hearingType")
        filed_raw = cls._first_value(row, "Case Filed Date", "dateFiled", "filedDate")
        hearing_raw = cls._first_value(
            row,
            "Hearing Date",
            "hearingDate",
            "hearingDateTime",
            "hearingStart",
        )
        case_data_id = cls._first_value(
            row,
            "caseDataID",
            "caseDataId",
            "case_data_id",
            "caseId",
        )

        filing_date = cls._parse_date_str(filed_raw) or court_today(COURT_TIMEZONE)
        court_date = cls._parse_date_str(hearing_raw)
        notice_type = case_type if not hearing_type else f"{case_type} / {hearing_type}"
        source_url = f"{CASE_DETAIL_BASE}/{case_data_id}" if case_data_id else CASE_DETAIL_BASE

        return Filing(
            case_number=case_number,
            tenant_name=cls._clean_party_name(tenant_name),
            property_address="Unknown",
            landlord_name=cls._clean_party_name(landlord_name) or "Unknown",
            filing_date=filing_date,
            court_date=court_date,
            state=STATE,
            county=cls._county_from_case_location(case_location),
            notice_type=notice_type,
            source_url=source_url,
        )

    @staticmethod
    def _prefer_filing(candidate: Filing, existing: Filing) -> bool:
        if existing.court_date is None:
            return candidate.court_date is not None
        if candidate.court_date is None:
            return False
        return candidate.court_date < existing.court_date

    @staticmethod
    def _first_value(row: dict, *keys: str) -> str:
        for key in keys:
            value = row.get(key)
            if value is None:
                continue
            value = str(value).strip()
            if value:
                return value
        return ""

    @staticmethod
    def _split_case_description(description: str) -> tuple[str, str]:
        if not description:
            return "", ""
        normalized = re.sub(r"\s+", " ", description).strip()
        parts = re.split(r"\s+v(?:s\.?|\.?)\s+", normalized, maxsplit=1, flags=re.I)
        if len(parts) != 2:
            return normalized, ""
        return parts[0].strip(), parts[1].strip()

    @staticmethod
    def _party_names_from_hit(row: dict) -> tuple[str, str]:
        plaintiff = ""
        defendant = ""
        parties = row.get("parties") if isinstance(row.get("parties"), list) else []
        for party in parties:
            if not isinstance(party, dict):
                continue
            role = str(party.get("partyTypeCode") or party.get("partyType") or "").lower()
            name = str(party.get("name") or party.get("partyName") or "").strip()
            if not name:
                continue
            if "plaintiff" in role and not plaintiff:
                plaintiff = name
            elif "defendant" in role and not defendant:
                defendant = name
        return plaintiff, defendant

    @staticmethod
    def _clean_party_name(name: str) -> str:
        name = re.sub(r"\s+", " ", name).strip(" ,")
        occupant_patterns = [
            r",?\s*and all other occupants\b.*",
            r",?\s*and all other tenants\b.*",
            r",?\s*and all others\b.*",
            r",?\s*all other occupants\b.*",
            r",?\s*all others\b.*",
        ]
        for pattern in occupant_patterns:
            name = re.sub(pattern, "", name, flags=re.I).strip(" ,")
        return name

    @staticmethod
    def _county_from_case_location(case_location: str) -> str:
        if not case_location:
            return "Georgia"
        county = case_location.split(" - ", 1)[0].strip()
        county = re.sub(r"\s+County$", "", county, flags=re.I).strip()
        return county or "Georgia"

    @staticmethod
    def _parse_date_str(raw: str) -> date | None:
        if not raw:
            return None
        raw = raw.split("T")[0].strip()
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%Y %I:%M:%S %p"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
        return None
