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


def test_parse_eviction_schedule_property_address_is_unknown():
    filings = _parse_eviction_schedule(SAMPLE_HTML, hearing_date=HEARING_DATE)

    for filing in filings:
        assert filing.property_address == "Unknown"


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
