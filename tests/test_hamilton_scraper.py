from __future__ import annotations

from datetime import date

from scrapers.ohio.hamilton import (
    HamiltonCountyMunicipalScraper,
    _parse_eviction_schedule,
    _strip_occupant_suffix,
)


SAMPLE_HTML = """
<html><body>
<table id="judge_schedule_table" width="100%" cellspacing="0" cellpadding="3" border="1">
<thead>
<tr><th colspan="4">Eviction Schedule</th></tr>
</thead>
<tbody>
<tr>
  <td style="background-color:#174c8c;color:white">Case #:</td>
  <td>26CV13828 <form action="case_summary.php"><input type="hidden" name="casenumber" value="26CV13828"></form></td>
  <td>Time: </td>
  <td>09:30</td>
</tr>
<tr>
  <td>Plaintiff:</td><td>NPRC APEX LLC</td>
  <td>Attorney for Plaintiff: </td><td>BREWER/KEVIN/R</td>
</tr>
<tr>
  <td>Defendant:</td><td>ALEASHA BOYCE</td>
  <td>Attorney for Defendant: </td><td></td>
</tr>
<tr>
  <td>Next Action:</td><td>HEARING</td><td></td><td></td>
</tr>
<tr>
  <td style="background-color:#174c8c;color:white">Case #:</td>
  <td>26CV13501 <form action="case_summary.php"><input type="hidden" name="casenumber" value="26CV13501"></form></td>
  <td>Time: </td>
  <td>10:00</td>
</tr>
<tr>
  <td>Plaintiff:</td><td>VILLAGE OAKS APARTMENTS LLC</td>
  <td>Attorney for Plaintiff: </td><td></td>
</tr>
<tr>
  <td>Defendant:</td><td>JOHN DOE et al</td>
  <td>Attorney for Defendant: </td><td></td>
</tr>
<tr>
  <td>Next Action:</td><td>DEFAULT</td><td></td><td></td>
</tr>
<tr>
  <td style="background-color:#174c8c;color:white">Case #:</td>
  <td>26CV13999 <form action="case_summary.php"><input type="hidden" name="casenumber" value="26CV13999"></form></td>
  <td>Time: </td>
  <td>11:00</td>
</tr>
<tr>
  <td>Plaintiff:</td><td>MANAGEMENT CORP</td>
  <td>Attorney for Plaintiff: </td><td></td>
</tr>
<tr>
  <td>Defendant:</td><td>JANE SMITH AND ALL OTHER OCCUPANTS</td>
  <td>Attorney for Defendant: </td><td></td>
</tr>
<tr>
  <td>Next Action:</td><td>HEARING</td><td></td><td></td>
</tr>
</tbody>
</table>
</body></html>
"""

HEARING_DATE = date(2026, 5, 13)


def test_parse_eviction_schedule_returns_all_cases():
    filings = _parse_eviction_schedule(SAMPLE_HTML, hearing_date=HEARING_DATE)

    assert len(filings) == 3


def test_parse_eviction_schedule_maps_case_number():
    filings = _parse_eviction_schedule(SAMPLE_HTML, hearing_date=HEARING_DATE)

    assert filings[0].case_number == "26CV13828"
    assert filings[1].case_number == "26CV13501"


def test_parse_eviction_schedule_maps_landlord_and_tenant():
    filings = _parse_eviction_schedule(SAMPLE_HTML, hearing_date=HEARING_DATE)

    assert filings[0].landlord_name == "NPRC APEX LLC"
    assert filings[0].tenant_name == "ALEASHA BOYCE"


def test_parse_eviction_schedule_sets_court_date_to_hearing_date():
    filings = _parse_eviction_schedule(SAMPLE_HTML, hearing_date=HEARING_DATE)

    assert filings[0].court_date == HEARING_DATE


def test_parse_eviction_schedule_sets_county_state_and_notice_type():
    filings = _parse_eviction_schedule(SAMPLE_HTML, hearing_date=HEARING_DATE)

    assert filings[0].state == "OH"
    assert filings[0].county == "Hamilton"
    assert filings[0].notice_type == "Eviction"


def test_parse_eviction_schedule_property_address_is_cincinnati_oh():
    filings = _parse_eviction_schedule(SAMPLE_HTML, hearing_date=HEARING_DATE)

    for filing in filings:
        assert filing.property_address == "Cincinnati, OH"


def test_parse_eviction_schedule_filing_date_falls_back_to_hearing_date():
    filings = _parse_eviction_schedule(SAMPLE_HTML, hearing_date=HEARING_DATE)

    assert filings[0].filing_date == HEARING_DATE


def test_parse_eviction_schedule_source_url_contains_date_and_court():
    filings = _parse_eviction_schedule(
        SAMPLE_HTML,
        hearing_date=HEARING_DATE,
        source_url="https://www.courtclerk.org/data/eviction_schedule.php?chosendate=5/13/2026&court=MCV&location=EVIM",
    )

    assert "5/13/2026" in filings[0].source_url


def test_strip_occupant_suffix_removes_et_al():
    assert _strip_occupant_suffix("JOHN DOE et al") == "JOHN DOE"


def test_strip_occupant_suffix_removes_and_all_other_occupants():
    assert _strip_occupant_suffix("JANE SMITH AND ALL OTHER OCCUPANTS") == "JANE SMITH"


def test_strip_occupant_suffix_removes_and_all_occupants():
    assert _strip_occupant_suffix("ADRIENNE R WHITE AND ALL OCCUPANTS") == "ADRIENNE R WHITE"


def test_strip_occupant_suffix_leaves_plain_names_unchanged():
    assert _strip_occupant_suffix("ALEASHA BOYCE") == "ALEASHA BOYCE"


def test_parse_eviction_schedule_strips_et_al_from_defendant():
    filings = _parse_eviction_schedule(SAMPLE_HTML, hearing_date=HEARING_DATE)

    assert filings[1].tenant_name == "JOHN DOE"


def test_parse_eviction_schedule_strips_and_all_other_occupants_from_defendant():
    filings = _parse_eviction_schedule(SAMPLE_HTML, hearing_date=HEARING_DATE)

    assert filings[2].tenant_name == "JANE SMITH"


def test_scraper_records_last_error_when_fetch_fails(monkeypatch):
    scraper = HamiltonCountyMunicipalScraper(lookback_days=2)

    def fail_get_text(_url: str) -> str:
        raise ConnectionResetError("connection reset")

    monkeypatch.setattr(scraper, "_get_text", fail_get_text)

    filings = scraper.scrape()

    assert filings == []
    assert "connection reset" in scraper.last_error


def test_scraper_dedupes_same_case_across_dates(monkeypatch):
    scraper = HamiltonCountyMunicipalScraper(lookback_days=2)

    call_count = 0

    def fake_get_text(_url: str) -> str:
        nonlocal call_count
        call_count += 1
        return SAMPLE_HTML

    monkeypatch.setattr(scraper, "_get_text", fake_get_text)

    filings = scraper.scrape()

    case_numbers = [f.case_number for f in filings]
    assert len(case_numbers) == len(set(case_numbers))


from scrapers.ohio.hamilton import _parse_party_address, _fetch_defendant_address


class TestParsePartyAddress:
    def test_standard_address_with_apt(self):
        from bs4 import BeautifulSoup
        html = '<td>1451 HILLCREST RD APT 2<br/>CINCINNATI OH 45224</td>'
        td = BeautifulSoup(html, 'html.parser').find('td')
        assert _parse_party_address(td) == "1451 HILLCREST RD APT 2, CINCINNATI, OH 45224"

    def test_standard_address_no_apt(self):
        from bs4 import BeautifulSoup
        html = '<td>2200 DANA AVE<br/>CINCINNATI OH 45207</td>'
        td = BeautifulSoup(html, 'html.parser').find('td')
        assert _parse_party_address(td) == "2200 DANA AVE, CINCINNATI, OH 45207"

    def test_nine_digit_zip_truncated(self):
        from bs4 import BeautifulSoup
        html = '<td>9918 CARVER RD STE 110<br/>CINCINNATI OH 452420000</td>'
        td = BeautifulSoup(html, 'html.parser').find('td')
        result = _parse_party_address(td)
        assert "45242" in result
        assert "452420000" not in result

    def test_empty_td_returns_empty(self):
        from bs4 import BeautifulSoup
        html = '<td></td>'
        td = BeautifulSoup(html, 'html.parser').find('td')
        assert _parse_party_address(td) == ""


class TestFetchDefendantAddress:
    def test_returns_first_defendant_address(self):
        from unittest.mock import MagicMock
        party_html = """
        <table id="party_info_table">
          <thead><tr><th>Name</th><th>Address</th><th>Party</th><th>Attorney</th><th>Address</th><th>ID</th></tr></thead>
          <tbody>
            <tr>
              <td>LANDLORD LLC</td>
              <td>100 MAIN ST<br/>CINCINNATI OH 45202</td>
              <td>P\xa01</td>
              <td></td><td></td><td></td>
            </tr>
            <tr>
              <td>JOHN DOE</td>
              <td>456 ELM ST APT 3<br/>CINCINNATI OH 45219</td>
              <td>D\xa01</td>
              <td colspan="3"></td>
            </tr>
          </tbody>
        </table>
        """
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = party_html
        mock_session.post.return_value = mock_resp

        result = _fetch_defendant_address(mock_session, "26CV11460")
        assert result == "456 ELM ST APT 3, CINCINNATI, OH 45219"

    def test_returns_none_on_exception(self):
        from unittest.mock import MagicMock
        mock_session = MagicMock()
        mock_session.post.side_effect = Exception("timeout")
        result = _fetch_defendant_address(mock_session, "26CV11460")
        assert result is None

    def test_returns_none_when_no_defendant_row(self):
        from unittest.mock import MagicMock
        party_html = """
        <table id="party_info_table">
          <thead><tr><th>Name</th><th>Address</th><th>Party</th></tr></thead>
          <tbody>
            <tr><td>LANDLORD</td><td>100 MAIN<br/>CINCINNATI OH 45202</td><td>P\xa01</td></tr>
          </tbody>
        </table>
        """
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = party_html
        mock_session.post.return_value = mock_resp
        result = _fetch_defendant_address(mock_session, "26CV11460")
        assert result is None

    def test_returns_none_on_non_200(self):
        from unittest.mock import MagicMock
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_session.post.return_value = mock_resp
        result = _fetch_defendant_address(mock_session, "26CV11460")
        assert result is None
