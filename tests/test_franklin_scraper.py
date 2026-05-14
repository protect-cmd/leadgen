from __future__ import annotations

from datetime import date

from scrapers.ohio.franklin import (
    FranklinCountyMunicipalScraper,
    _discover_report_links,
    _parse_eviction_csv,
)


CSV_TEXT = """"CASE_NUMBER","CASE_FILE_DATE","LAST_DISPOSITION_DATE","LAST_DISPOSITION_DESCRIPTION","FIRST_PLAINTIFF_PARTY_SEQUENCE","FIRST_PLAINTIFF_FIRST_NAME","FIRST_PLAINTIFF_MIDDLE_NAME","FIRST_PLAINTIFF_LAST_NAME","FIRST_PLAINTIFF_SUFFIX_NAME","FIRST_PLAINTIFF_COMPANY_NAME","FIRST_PLAINTIFF_ADDRESS_LINE_1","FIRST_PLAINTIFF_ADDRESS_LINE_2","FIRST_PLAINTIFF_CITY","FIRST_PLAINTIFF_STATE","FIRST_PLAINTIFF_ZIP","FIRST_DEFENDANT_PARTY_SEQUENCE","FIRST_DEFENDANT_FIRST_NAME","FIRST_DEFENDANT_MIDDLE_NAME","FIRST_DEFENDANT_LAST_NAME","FIRST_DEFENDANT_SUFFIX_NAME","FIRST_DEFENDANT_COMPANY_NAME","FIRST_DEFENDANT_ADDRESS_LINE_1","FIRST_DEFENDANT_ADDRESS_LINE_2","FIRST_DEFENDANT_CITY","FIRST_DEFENDANT_STATE","FIRST_DEFENDANT_ZIP"
"2026 CVG 025287","05/01/2026","","UNDISPOSED","1","","","","","VILLAGE COURT","PO BOX 2290","","COLUMBUS","OH","43216","2","DESON","","JOHNSON","","","4638 TAMARACK BOULEVARD APT B12","","COLUMBUS","OH","43229"
"2026 CVG 025291","05/01/2026","","UNDISPOSED","1","","","","","QUEST MANAGEMENT","PO BOX 2290","","COLUMBUS","OH","43216","2","CHARDONNAY","","BYERS","","","67 MAYFAIR BOULEVARD APT D","","COLUMBUS","","43213"
"""


def test_parse_eviction_csv_maps_defendant_address_to_filing():
    filings = _parse_eviction_csv(CSV_TEXT, source_url="https://example.com/may.csv")

    assert len(filings) == 2
    assert filings[0].case_number == "2026 CVG 025287"
    assert filings[0].filing_date == date(2026, 5, 1)
    assert filings[0].tenant_name == "DESON JOHNSON"
    assert filings[0].landlord_name == "VILLAGE COURT"
    assert filings[0].property_address == "4638 TAMARACK BOULEVARD APT B12, COLUMBUS, OH 43229"
    assert filings[0].state == "OH"
    assert filings[0].county == "Franklin"
    assert filings[0].notice_type == "Civil F.E.D. Eviction"
    assert filings[0].source_url == "https://example.com/may.csv"


def test_parse_eviction_csv_defaults_missing_defendant_state_to_oh():
    filings = _parse_eviction_csv(CSV_TEXT, source_url="https://example.com/may.csv")

    assert filings[1].property_address == "67 MAYFAIR BOULEVARD APT D, COLUMBUS, OH 43213"


def test_discover_report_links_filters_months_in_lookback_window():
    html = """
    <a href="/storage/shared/civil-fed/FCMC Civil F.E.D. (Eviction) Case List 2026-05-01 to 2026-05-31.csv?111">May</a>
    <a href="/storage/shared/civil-fed/FCMC Civil F.E.D. (Eviction) Case List 2026-04-01 to 2026-04-30.csv?222">April</a>
    <a href="/storage/shared/civil-fed/FCMC Civil F.E.D. (Eviction) Case List 2026-03-01 to 2026-03-31.csv?333">March</a>
    """

    links = _discover_report_links(
        html,
        today=date(2026, 5, 14),
        lookback_days=20,
    )

    assert [link.month_start for link in links] == [date(2026, 5, 1), date(2026, 4, 1)]
    assert links[0].url.startswith("https://www.fcmcclerk.com/storage/shared/civil-fed/")
    assert "%20" in links[0].url


def test_scraper_records_last_error_when_report_index_fails(monkeypatch):
    scraper = FranklinCountyMunicipalScraper(lookback_days=2)

    def fail_get_text(url: str) -> str:
        raise ConnectionResetError("connection reset")

    monkeypatch.setattr(scraper, "_get_text", fail_get_text)

    filings = scraper.scrape()

    assert filings == []
    assert scraper.last_error == "failed to fetch FCMC eviction report index: connection reset"
