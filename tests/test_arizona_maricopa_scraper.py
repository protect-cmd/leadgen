from __future__ import annotations

from datetime import date

from scrapers.arizona.maricopa import (
    MaricopaCalendarCase,
    MaricopaCaseDetail,
    MaricopaJusticeCourtScraper,
    _parse_calendar_html,
    _parse_case_detail_html,
)
from scrapers.arizona.maricopa_assessor import AddressMatchResult, ParcelRecord


CALENDAR_HTML = """
<div id="MainContent_CourtCalendarRepeater_DivCaseCalendarWrapper_338" class="d-flex jc-case-calendar-info jc-case-events altRow">
  <div class="jc-cc-case-number jc-t-data">
    <a href="CaseInfo.aspx?casenumber=CC2026121247000">CC2026121247</a>
  </div>
  <div class="jc-cc-case-date jc-t-data">5/15/2026</div>
  <div class="jc-cc-case-time jc-t-data">1:00 PM</div>
  <div class="jc-cc-case-title jc-t-data">
    Eviction Action Hearing
     - Attorney
  </div>
  <div class="jc-cc-case-party jc-t-data">
    <span class="jc-split-long">HARMONY AT THE PARK 3</span>
  </div>
</div>
<div id="MainContent_CourtCalendarRepeater_DivCaseCalendarWrapper_339" class="d-flex jc-case-calendar-info jc-case-events altRow">
  <div class="jc-cc-case-party2-wrapper">
    <div class="jc-party-name-spacer">&nbsp;</div>
    <div class="jc-cc-case-party2 jc-t-data">
      <span class="jc-split-long">TIMOTHY  MCCULLUM</span>
    </div>
  </div>
</div>
<div id="MainContent_CourtCalendarRepeater_DivCaseCalendarWrapper_340" class="d-flex jc-case-calendar-info jc-case-events">
  <div class="jc-cc-case-number jc-t-data">
    <a href="CaseInfo.aspx?casenumber=CV2026000001000">CV2026000001</a>
  </div>
  <div class="jc-cc-case-title jc-t-data">Small Claims Hearing</div>
</div>
"""


DETAIL_HTML = """
<div>
  Case Number: CC2026121247
  Judge: Sama, Jennifer
  File Date: 5/11/2026
  Location: El Centro Justice Court
  Case Type: Justice Civil
  Case Status: 01 - New Case
  Party Information
  Plaintiff Party Name HARMONY AT THE PARK 3 Relationship Plaintiff
  Defendant Party Name TIMOTHY MCCULLUM Relationship Defendant
  Case Calendar Date Time Event Result 5/15/2026 1:00 Eviction Action Hearing
</div>
"""


def test_parse_calendar_html_returns_eviction_hearing_cases_only():
    cases = _parse_calendar_html(
        CALENDAR_HTML,
        court_name="El Centro",
        calendar_url="https://justicecourts.maricopa.gov/app/courtrecords/CourtCalendar?id=3822",
    )

    assert cases == [
        MaricopaCalendarCase(
            case_number="CC2026121247",
            court_name="El Centro",
            court_date=date(2026, 5, 15),
            court_time="1:00 PM",
            notice_type="Eviction Action Hearing - Attorney",
            landlord_name="HARMONY AT THE PARK 3",
            tenant_name="TIMOTHY MCCULLUM",
            detail_path="CaseInfo.aspx?casenumber=CC2026121247000",
            calendar_url="https://justicecourts.maricopa.gov/app/courtrecords/CourtCalendar?id=3822",
        )
    ]


def test_parse_case_detail_html_extracts_file_date_and_status():
    detail = _parse_case_detail_html(DETAIL_HTML)

    assert detail.filing_date == date(2026, 5, 11)
    assert detail.status == "01 - New Case"


def test_build_filing_uses_unknown_address_until_source_exposes_it():
    scraper = MaricopaJusticeCourtScraper()
    case = _parse_calendar_html(CALENDAR_HTML, court_name="El Centro", calendar_url="calendar-url")[0]
    detail = _parse_case_detail_html(DETAIL_HTML)

    filing = scraper._build_filing(case, detail, "https://example.com/detail")

    assert filing.case_number == "CC2026121247"
    assert filing.state == "AZ"
    assert filing.county == "Maricopa"
    assert filing.filing_date == date(2026, 5, 11)
    assert filing.court_date == date(2026, 5, 15)
    assert filing.landlord_name == "HARMONY AT THE PARK 3"
    assert filing.tenant_name == "TIMOTHY MCCULLUM"
    assert filing.property_address == "Unknown"
    assert filing.source_url == "https://example.com/detail"


def test_scrape_respects_max_cases_for_smoke_runs(monkeypatch):
    scraper = MaricopaJusticeCourtScraper(max_cases=1)

    second_calendar_html = CALENDAR_HTML.replace("CC2026121247", "CC2026121248").replace(
        "TIMOTHY  MCCULLUM",
        "JANE  TENANT",
    )

    monkeypatch.setattr(
        scraper,
        "_fetch_court_links",
        lambda: [
            ("El Centro", "https://example.com/calendar-one"),
            ("Maryvale", "https://example.com/calendar-two"),
        ],
    )

    def fake_get(url: str) -> str:
        if url == "https://example.com/calendar-one":
            return CALENDAR_HTML
        if url == "https://example.com/calendar-two":
            return second_calendar_html
        return DETAIL_HTML

    monkeypatch.setattr(scraper, "_get", fake_get)

    filings = scraper.scrape()

    assert len(filings) == 1
    assert filings[0].case_number == "CC2026121247"


def test_scrape_tracks_address_match_status_by_case(monkeypatch):
    class FakeAssessor:
        def match_owner(self, landlord_name: str) -> AddressMatchResult:
            return AddressMatchResult(
                status="single_match",
                query_variant=landlord_name,
                records=[
                    ParcelRecord(
                        apn="123-45-678",
                        owner_name=landlord_name,
                        physical_address="123 W MAIN ST PHOENIX 85001",
                        mailing_address="",
                        physical_city="PHOENIX",
                        physical_zip="85001",
                        jurisdiction="PHOENIX",
                    )
                ],
            )

    scraper = MaricopaJusticeCourtScraper(
        max_cases=1,
        enrich_addresses=True,
        assessor_client=FakeAssessor(),
    )
    monkeypatch.setattr(scraper, "_fetch_court_links", lambda: [("El Centro", "calendar-url")])
    monkeypatch.setattr(scraper, "_get", lambda url: CALENDAR_HTML if url == "calendar-url" else DETAIL_HTML)

    filings = scraper.scrape()

    assert filings[0].property_address == "123 W MAIN ST PHOENIX 85001"
    assert scraper.address_match_counts == {
        "single_match": 1,
        "ambiguous": 0,
        "no_match": 0,
        "error": 0,
    }
    assert scraper.address_matches_by_case["CC2026121247"].status == "single_match"


def test_build_filing_uses_single_assessor_match_address_only():
    scraper = MaricopaJusticeCourtScraper(enrich_addresses=True)
    case = _parse_calendar_html(CALENDAR_HTML, court_name="El Centro", calendar_url="calendar-url")[0]
    detail = MaricopaCaseDetail(
        filing_date=date(2026, 5, 11),
        status="01 - New Case",
        address_match=AddressMatchResult(
            status="single_match",
            query_variant="HARMONY AT THE PARK 3",
            records=[
                ParcelRecord(
                    apn="123-45-678",
                    owner_name="HARMONY AT THE PARK 3",
                    physical_address="123 W MAIN ST PHOENIX 85001",
                    mailing_address="PO BOX 1 PHOENIX AZ 85001",
                    physical_city="PHOENIX",
                    physical_zip="85001",
                    jurisdiction="PHOENIX",
                )
            ],
        ),
    )

    filing = scraper._build_filing(case, detail, "https://example.com/detail")

    assert filing.property_address == "123 W MAIN ST PHOENIX 85001"


def test_build_filing_keeps_unknown_address_for_ambiguous_match():
    scraper = MaricopaJusticeCourtScraper(enrich_addresses=True)
    case = _parse_calendar_html(CALENDAR_HTML, court_name="El Centro", calendar_url="calendar-url")[0]
    detail = MaricopaCaseDetail(
        filing_date=date(2026, 5, 11),
        status="01 - New Case",
        address_match=AddressMatchResult(
            status="ambiguous",
            query_variant="HARMONY AT THE PARK 3",
            records=[
                ParcelRecord(
                    apn="123-45-678",
                    owner_name="HARMONY AT THE PARK 3",
                    physical_address="123 W MAIN ST PHOENIX 85001",
                    mailing_address="",
                    physical_city="PHOENIX",
                    physical_zip="85001",
                    jurisdiction="PHOENIX",
                ),
                ParcelRecord(
                    apn="123-45-679",
                    owner_name="HARMONY AT THE PARK 3",
                    physical_address="125 W MAIN ST PHOENIX 85001",
                    mailing_address="",
                    physical_city="PHOENIX",
                    physical_zip="85001",
                    jurisdiction="PHOENIX",
                ),
            ],
        ),
    )

    filing = scraper._build_filing(case, detail, "https://example.com/detail")

    assert filing.property_address == "Unknown"
