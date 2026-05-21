from __future__ import annotations

from datetime import date

import pytest

from scrapers.georgia.researchga import DISPOSSESSORY_CASE_TYPES, ReSearchGAScraper


class FakeLoginPage:
    def __init__(self) -> None:
        self.actions: list[tuple[str, str]] = []

    async def goto(self, url: str, **kwargs) -> None:
        self.actions.append(("goto", url))

    async def wait_for_timeout(self, ms: int) -> None:
        self.actions.append(("wait", str(ms)))

    async def fill(self, selector: str, value: str) -> None:
        self.actions.append(("fill", selector))

    async def click(self, selector: str) -> None:
        self.actions.append(("click", selector))


@pytest.mark.asyncio
async def test_researchga_login_uses_current_tyler_identity_fields(monkeypatch):
    monkeypatch.setenv("RESEARCHGA_EMAIL", "person@example.com")
    monkeypatch.setenv("RESEARCHGA_PASSWORD", "secret-password")

    page = FakeLoginPage()
    scraper = ReSearchGAScraper()

    await scraper._login(page)

    assert ("click", "text=Sign in with Your eFileGA Account") in page.actions
    assert ("fill", "#UserName") in page.actions
    assert ("fill", "#Password") in page.actions
    assert ("click", "button:has-text('Sign In')") in page.actions


def test_researchga_hearings_payload_matches_export_search_shape():
    payload = ReSearchGAScraper._build_hearings_payload(
        from_date=date(2026, 4, 30),
        to_date=date(2026, 6, 11),
        page_index=2,
        page_size=250,
    )

    assert payload["searchIndexType"] == "Hearings"
    assert payload["pageIndex"] == 2
    assert payload["pageSize"] == 250
    assert payload["sortFieldOrder"] == "desc"
    assert payload["sortFields"] == "0"

    conditions = payload["advancedSearchConditions"]["advancedSearchConditions"]
    assert conditions[0]["fieldOption"] == 10
    assert conditions[0]["valueSet"] == DISPOSSESSORY_CASE_TYPES
    assert conditions[1]["fieldOption"] == 0
    assert conditions[1]["fromValue"] == "04/30/2026"
    assert conditions[1]["toValue"] == "06/11/2026"


def test_researchga_chunks_hearing_date_window_to_avoid_tyler_row_cap():
    windows = ReSearchGAScraper._date_windows(
        from_date=date(2026, 5, 11),
        to_date=date(2026, 6, 27),
        window_days=7,
    )

    assert windows[0] == (date(2026, 5, 11), date(2026, 5, 17))
    assert windows[1] == (date(2026, 5, 18), date(2026, 5, 24))
    assert windows[-1] == (date(2026, 6, 22), date(2026, 6, 27))


def test_researchga_builds_filing_from_export_style_hearing_row():
    row = {
        "Hearing Date": "5/26/2026 9:00:00 AM",
        "Hearing Type": "DISPOSSESSORY PRO SE MEDIATION",
        "Case Description": "58 PLACE, LLC DWELL AT THE VIEW  vs.  ANGELA BOONE,AND ALL OTHER OCCUPANTS",
        "Case Number": "26ED386250",
        "Case Location": "Fulton - Magistrate Court",
        "Case Type": "Dispossessory",
        "Case Filed Date": "5/7/2026",
    }

    filing = ReSearchGAScraper._build_filing_from_hearing(row)

    assert filing is not None
    assert filing.case_number == "26ED386250"
    assert filing.landlord_name == "58 PLACE, LLC DWELL AT THE VIEW"
    assert filing.tenant_name == "ANGELA BOONE"
    assert filing.property_address == "Unknown"
    assert filing.filing_date == date(2026, 5, 7)
    assert filing.court_date == date(2026, 5, 26)
    assert filing.state == "GA"
    assert filing.county == "Fulton"
    assert filing.notice_type == "Dispossessory / DISPOSSESSORY PRO SE MEDIATION"


def test_researchga_builds_filing_from_multiline_vs_case_description():
    row = {
        "Hearing Date": "5/21/2026 10:30:00 AM",
        "Hearing Type": "Dispossessory Trial",
        "Case Description": "PIERCE INVESTMENT PROPERTIES LLC\nVS\nLARRY MILLER",
        "Case Number": "26-D-0892",
        "Case Location": "Spalding County - Magistrate Court",
        "Case Type": "Dispossessory - Possession Only",
        "Case Filed Date": "4/30/2026",
    }

    filing = ReSearchGAScraper._build_filing_from_hearing(row)

    assert filing is not None
    assert filing.landlord_name == "PIERCE INVESTMENT PROPERTIES LLC"
    assert filing.tenant_name == "LARRY MILLER"
    assert filing.county == "Spalding"


def test_researchga_builds_filing_from_api_style_hearing_hit():
    hit = {
        "hearingDate": "2026-05-26T09:00:00",
        "hearingType": "DISPOSSESSORY PRO SE MEDIATION",
        "caseNumber": "26ED386250",
        "caseDataID": "abc-123",
        "jurisdiction": "Fulton - Magistrate Court",
        "caseType": "Dispossessory",
        "dateFiled": "2026-05-07T00:00:00",
        "parties": [
            {"partyTypeCode": "Plaintiff", "name": "58 PLACE, LLC DWELL AT THE VIEW"},
            {"partyTypeCode": "Defendant", "name": "ANGELA BOONE,AND ALL OTHER OCCUPANTS"},
        ],
    }

    filing = ReSearchGAScraper._build_filing_from_hearing(hit)

    assert filing is not None
    assert filing.case_number == "26ED386250"
    assert filing.landlord_name == "58 PLACE, LLC DWELL AT THE VIEW"
    assert filing.tenant_name == "ANGELA BOONE"
    assert filing.filing_date == date(2026, 5, 7)
    assert filing.court_date == date(2026, 5, 26)
    assert filing.source_url.endswith("/abc-123")


def test_researchga_builds_filing_from_live_api_hearing_hit_shape():
    hit = {
        "hearingStart": "2026-05-21T10:30:00",
        "hearingType": "Dispossessory Trial",
        "caseNumber": "26-D-0885",
        "caseId": "2bd9ba58f8d24a6aa07eeb403c01dd22",
        "caseJurisdiction": "Spalding County - Magistrate Court",
        "caseTypeCode": "Dispossessory",
        "caseDescription": "T. ADDIS\nVS\nJAMES ATKINSON",
        "address": None,
    }

    filing = ReSearchGAScraper._build_filing_from_hearing(hit)

    assert filing is not None
    assert filing.case_number == "26-D-0885"
    assert filing.landlord_name == "T. ADDIS"
    assert filing.tenant_name == "JAMES ATKINSON"
    assert filing.county == "Spalding"
    assert filing.court_date == date(2026, 5, 21)
    assert filing.notice_type == "Dispossessory / Dispossessory Trial"
    assert filing.source_url.endswith("/2bd9ba58f8d24a6aa07eeb403c01dd22")


def test_researchga_dedupes_hearings_by_case_number():
    rows = [
        {
            "Hearing Date": "5/21/2026 10:30:00 AM",
            "Hearing Type": "Dispossessory Trial",
            "Case Description": "A LLC VS JANE TENANT",
            "Case Number": "26-D-0892",
            "Case Location": "Spalding County - Magistrate Court",
            "Case Type": "Dispossessory",
            "Case Filed Date": "4/30/2026",
        },
        {
            "Hearing Date": "5/22/2026 10:30:00 AM",
            "Hearing Type": "WRIT HEARING",
            "Case Description": "A LLC VS JANE TENANT",
            "Case Number": "26-D-0892",
            "Case Location": "Spalding County - Magistrate Court",
            "Case Type": "Dispossessory",
            "Case Filed Date": "4/30/2026",
        },
    ]

    filings = ReSearchGAScraper._build_filings_from_hearings(rows)

    assert len(filings) == 1
    assert filings[0].case_number == "26-D-0892"
    assert filings[0].court_date == date(2026, 5, 21)
