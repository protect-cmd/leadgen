from __future__ import annotations

from datetime import date

from scrapers.nevada.clark import (
    ClarkCountyJusticeCourtScraper,
    _parse_listable_events,
    _strip_occupant_suffix,
)

# ---------------------------------------------------------------------------
# Fixtures — actual HTML structure from CourtView listTable
# ---------------------------------------------------------------------------

SAMPLE_LIST_HTML = """
<table class="listTable" id="id6c">
<thead>
  <tr class="headers">
    <th>Start</th><th>Event Type</th><th>Judge</th><th>Location</th><th>Case Description</th>
  </tr>
</thead>
<tbody>
  <tr>
    <td>Showing 1 to 15 of 45 &lt;&lt; &lt; 1 2 3 &gt; &gt;&gt;</td>
  </tr>
  <tr>
    <td>09:30 AM</td>
    <td>EVICTION HEARING</td>
    <td>GEORGE, STEPHEN L</td>
    <td>DEPARTMENT 2</td>
    <td>26EH001210               ALBULM UNION VILLAGE  VS  FOX, KEVIN et al      OPEN</td>
  </tr>
  <tr>
    <td>09:30 AM</td>
    <td>MOTION TO PLACE ON CALENDAR - EVICTION</td>
    <td>GEORGE, STEPHEN L</td>
    <td>DEPARTMENT 2</td>
    <td>25EH004440               KEY PROPERTY MANAGEMENT  VS  SIMMONS, TRIANNA      REOPEN/REACTIVATED</td>
  </tr>
  <tr>
    <td>09:30 AM</td>
    <td>EVICTION HEARING</td>
    <td>GEORGE, STEPHEN L</td>
    <td>DEPARTMENT 2</td>
    <td>26EH001220               EMPIRE APARTMENTS  VS  WILLIAMS, JAILAH      OPEN</td>
  </tr>
  <tr>
    <td>08:00 AM</td>
    <td>PRETRIAL CUSTODY STATUS HEARING NLV</td>
    <td>HARRIS, BELINDA T</td>
    <td>DEPARTMENT 3</td>
    <td>26PCN001098-0000          WILLIAMS, CHRISTOPHER ALLEN</td>
  </tr>
  <tr>
    <td>09:00 AM</td>
    <td>EVICTION 5 DAY UNLAWFUL DETAINER</td>
    <td>HOO, KALANI</td>
    <td>DEPARTMENT 1</td>
    <td>26EH000999               DESERT VISTA LLC  VS  GARCIA, MARIA AND ALL OTHER OCCUPANTS      OPEN</td>
  </tr>
  <tr>
    <td>10:00 AM</td>
    <td>SUM EVIC 5 DAY PAY OR QUIT</td>
    <td>COOPER, JONATHAN</td>
    <td>DEPARTMENT 4</td>
    <td>26EH002345                VS  TENANT ONLY      OPEN</td>
  </tr>
</tbody>
</table>
"""

SAMPLE_NO_EVICTIONS_HTML = """
<table class="listTable" id="id6c">
<thead>
  <tr class="headers">
    <th>Start</th><th>Event Type</th><th>Judge</th><th>Location</th><th>Case Description</th>
  </tr>
</thead>
<tbody>
  <tr>
    <td>08:00 AM</td>
    <td>PRETRIAL CUSTODY STATUS HEARING NLV</td>
    <td>HARRIS, BELINDA T</td>
    <td>DEPARTMENT 3</td>
    <td>26PCN001098-0000          WILLIAMS, CHRISTOPHER ALLEN</td>
  </tr>
</tbody>
</table>
"""

SAMPLE_NO_TABLE_HTML = "<div>No events scheduled</div>"

HEARING_DATE = date(2026, 5, 13)
SOURCE_URL = "https://cvpublicaccess.clarkcountynv.gov/eservices/calendar.page"


# ---------------------------------------------------------------------------
# _strip_occupant_suffix
# ---------------------------------------------------------------------------

class TestStripOccupantSuffix:
    def test_strips_et_al(self):
        assert _strip_occupant_suffix("FOX, KEVIN et al") == "FOX, KEVIN"

    def test_strips_et_al_with_period(self):
        assert _strip_occupant_suffix("SMITH, JOHN ET AL.") == "SMITH, JOHN"

    def test_strips_and_all_other_occupants(self):
        assert _strip_occupant_suffix("DOE, JANE AND ALL OTHER OCCUPANTS") == "DOE, JANE"

    def test_strips_and_all_other_tenants(self):
        assert _strip_occupant_suffix("DOE, JANE AND ALL OTHER TENANTS") == "DOE, JANE"

    def test_strips_and_all_others(self):
        assert _strip_occupant_suffix("SMITH, BOB AND ALL OTHERS") == "SMITH, BOB"

    def test_no_suffix_unchanged(self):
        assert _strip_occupant_suffix("GARCIA, MARIA") == "GARCIA, MARIA"

    def test_empty_string(self):
        assert _strip_occupant_suffix("") == ""

    def test_case_insensitive(self):
        assert _strip_occupant_suffix("jones, tom et al.") == "jones, tom"


# ---------------------------------------------------------------------------
# _parse_listable_events
# ---------------------------------------------------------------------------

class TestParseListableEvents:
    def test_returns_only_eviction_filings(self):
        filings = _parse_listable_events(SAMPLE_LIST_HTML, hearing_date=HEARING_DATE, source_url=SOURCE_URL)
        # Should exclude criminal PRETRIAL CUSTODY row
        for f in filings:
            assert "EVIC" in f.notice_type.upper() or "SUM EVIC" in f.notice_type.upper()

    def test_parses_correct_count(self):
        filings = _parse_listable_events(SAMPLE_LIST_HTML, hearing_date=HEARING_DATE, source_url=SOURCE_URL)
        # 3 EVICTION HEARING + 1 MOTION TO PLACE + 1 EUD + 1 SUM EVIC = 5 eviction rows (minus 1 no-landlord = still 5 since landlord=Unknown)
        assert len(filings) == 5

    def test_case_number_parsed(self):
        filings = _parse_listable_events(SAMPLE_LIST_HTML, hearing_date=HEARING_DATE, source_url=SOURCE_URL)
        case_numbers = [f.case_number for f in filings]
        assert "26EH001210" in case_numbers
        assert "25EH004440" in case_numbers
        assert "26EH001220" in case_numbers

    def test_landlord_extracted(self):
        filings = _parse_listable_events(SAMPLE_LIST_HTML, hearing_date=HEARING_DATE, source_url=SOURCE_URL)
        by_case = {f.case_number: f for f in filings}
        assert by_case["26EH001210"].landlord_name == "ALBULM UNION VILLAGE"
        assert by_case["25EH004440"].landlord_name == "KEY PROPERTY MANAGEMENT"
        assert by_case["26EH001220"].landlord_name == "EMPIRE APARTMENTS"

    def test_tenant_extracted_and_suffix_stripped(self):
        filings = _parse_listable_events(SAMPLE_LIST_HTML, hearing_date=HEARING_DATE, source_url=SOURCE_URL)
        by_case = {f.case_number: f for f in filings}
        assert by_case["26EH001210"].tenant_name == "FOX, KEVIN"
        assert by_case["25EH004440"].tenant_name == "SIMMONS, TRIANNA"
        assert by_case["26EH001220"].tenant_name == "WILLIAMS, JAILAH"

    def test_tenant_occupant_suffix_stripped(self):
        filings = _parse_listable_events(SAMPLE_LIST_HTML, hearing_date=HEARING_DATE, source_url=SOURCE_URL)
        by_case = {f.case_number: f for f in filings}
        assert by_case["26EH000999"].tenant_name == "GARCIA, MARIA"

    def test_status_stripped_from_tenant(self):
        filings = _parse_listable_events(SAMPLE_LIST_HTML, hearing_date=HEARING_DATE, source_url=SOURCE_URL)
        by_case = {f.case_number: f for f in filings}
        assert "OPEN" not in by_case["26EH001210"].tenant_name
        assert "REOPEN" not in by_case["25EH004440"].tenant_name

    def test_hearing_date_is_court_date(self):
        filings = _parse_listable_events(SAMPLE_LIST_HTML, hearing_date=HEARING_DATE, source_url=SOURCE_URL)
        for f in filings:
            assert f.court_date == HEARING_DATE

    def test_filing_date_proxied_from_hearing_date(self):
        filings = _parse_listable_events(SAMPLE_LIST_HTML, hearing_date=HEARING_DATE, source_url=SOURCE_URL)
        for f in filings:
            assert f.filing_date == HEARING_DATE

    def test_property_address_always_unknown(self):
        filings = _parse_listable_events(SAMPLE_LIST_HTML, hearing_date=HEARING_DATE, source_url=SOURCE_URL)
        for f in filings:
            assert f.property_address == "Unknown"

    def test_state_is_nv(self):
        filings = _parse_listable_events(SAMPLE_LIST_HTML, hearing_date=HEARING_DATE, source_url=SOURCE_URL)
        for f in filings:
            assert f.state == "NV"

    def test_county_is_clark(self):
        filings = _parse_listable_events(SAMPLE_LIST_HTML, hearing_date=HEARING_DATE, source_url=SOURCE_URL)
        for f in filings:
            assert f.county == "Clark"

    def test_notice_type_contains_event_type(self):
        filings = _parse_listable_events(SAMPLE_LIST_HTML, hearing_date=HEARING_DATE, source_url=SOURCE_URL)
        by_case = {f.case_number: f for f in filings}
        assert "EVICTION HEARING" in by_case["26EH001210"].notice_type

    def test_source_url_preserved(self):
        filings = _parse_listable_events(SAMPLE_LIST_HTML, hearing_date=HEARING_DATE, source_url=SOURCE_URL)
        for f in filings:
            assert f.source_url == SOURCE_URL

    def test_no_evictions_returns_empty(self):
        filings = _parse_listable_events(SAMPLE_NO_EVICTIONS_HTML, hearing_date=HEARING_DATE, source_url=SOURCE_URL)
        assert filings == []

    def test_missing_table_returns_empty(self):
        filings = _parse_listable_events(SAMPLE_NO_TABLE_HTML, hearing_date=HEARING_DATE, source_url=SOURCE_URL)
        assert filings == []

    def test_skips_rows_without_vs_separator(self):
        html = """
        <table class="listTable">
          <tr><td>09:00 AM</td><td>EVICTION HEARING</td><td>JUDGE</td><td>DEPT 1</td>
              <td>26EH099999               PLAINTIFF ONLY NO VS</td></tr>
        </table>
        """
        filings = _parse_listable_events(html, hearing_date=HEARING_DATE, source_url=SOURCE_URL)
        assert len(filings) == 0

    def test_unknown_landlord_fallback(self):
        filings = _parse_listable_events(SAMPLE_LIST_HTML, hearing_date=HEARING_DATE, source_url=SOURCE_URL)
        by_case = {f.case_number: f for f in filings}
        assert by_case["26EH002345"].landlord_name == "Unknown"

    def test_unknown_tenant_fallback_when_empty(self):
        html = """
        <table class="listTable">
          <tr><td>09:00 AM</td><td>EVICTION HEARING</td><td>JUDGE</td><td>DEPT 1</td>
              <td>26EH099998               LANDLORD LLC  VS       OPEN</td></tr>
        </table>
        """
        filings = _parse_listable_events(html, hearing_date=HEARING_DATE, source_url=SOURCE_URL)
        assert len(filings) == 1
        assert filings[0].tenant_name == "Unknown"

    def test_skips_pagination_row(self):
        filings = _parse_listable_events(SAMPLE_LIST_HTML, hearing_date=HEARING_DATE, source_url=SOURCE_URL)
        # Pagination row has only 1 td — must not be counted
        for f in filings:
            assert "Showing" not in f.case_number


# ---------------------------------------------------------------------------
# ClarkCountyJusticeCourtScraper (unit — no network)
# ---------------------------------------------------------------------------

class TestClarkCountyJusticeCourtScraperUnit:
    def test_default_lookback_days(self):
        scraper = ClarkCountyJusticeCourtScraper()
        assert scraper.lookback_days == 2

    def test_custom_lookback_days(self):
        scraper = ClarkCountyJusticeCourtScraper(lookback_days=5)
        assert scraper.lookback_days == 5

    def test_last_error_initially_none(self):
        scraper = ClarkCountyJusticeCourtScraper()
        assert scraper.last_error is None

    def test_max_cases_default(self):
        scraper = ClarkCountyJusticeCourtScraper()
        assert scraper.max_cases is None or isinstance(scraper.max_cases, int)

    def test_max_cases_custom(self):
        scraper = ClarkCountyJusticeCourtScraper(max_cases=50)
        assert scraper.max_cases == 50
