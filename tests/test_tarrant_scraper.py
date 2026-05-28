from __future__ import annotations

from datetime import date

import pytest

from scrapers.texas.tarrant import (
    TarrantCountyJPScraper,
    _clean_tenant,
    _parse_case_detail,
    _parse_results_page,
    _parse_style,
)

# ---------------------------------------------------------------------------
# HTML fixtures — based on actual Tarrant Odyssey portal responses
# ---------------------------------------------------------------------------

# Case Records Search results page (mix of EFile Evictions and EFile Debt Claims)
SAMPLE_RESULTS_HTML = """
<html><body>
<table>
  <thead>
    <tr>
      <th>Case Number</th><th>Citation</th><th>Style/Defendant Info</th>
      <th>Filed/Location</th><th>Type/Status</th><th>Charges</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><a href="CaseDetail.aspx?CaseID=6034068">JP01-26-E00110781</a></td>
      <td></td>
      <td><div>Bel Fossil LP vs. Jennifer Phillips,Justin Emerson AND ALL OCCUPANTS</div></td>
      <td><div>05/14/2026</div><div>JP No. 1</div></td>
      <td valign="top" nowrap="true"><div>EFile Evictions</div><div>Filed</div></td>
      <td></td>
    </tr>
    <tr>
      <td><a href="CaseDetail.aspx?CaseID=6032230">JP01-26-DC00038820</a></td>
      <td></td>
      <td><div>Barclays Bank Delaware vs. Rodolfo Martinez</div></td>
      <td><div>05/08/2026</div><div>JP No. 1</div></td>
      <td valign="top" nowrap="true"><div>EFile Debt Claims</div><div>Filed</div></td>
      <td></td>
    </tr>
    <tr>
      <td><a href="CaseDetail.aspx?CaseID=6034100">JP01-26-E00110795</a></td>
      <td></td>
      <td><div>ASD HC Fossil Creek I Fee Owner LLC vs. Alex Ridge,Jasker Ridge AND ALL OCCUPANTS</div></td>
      <td><div>05/08/2026</div><div>JP No. 1</div></td>
      <td valign="top" nowrap="true"><div>EFile Evictions</div><div>Dismissed</div></td>
      <td></td>
    </tr>
    <tr>
      <td><a href="CaseDetail.aspx?CaseID=6034200">JP02-26-E00110900</a></td>
      <td></td>
      <td><div>Spring Lake Village LLC vs. VINCENT LEE MCGEE AND ALL OCCUPANTS</div></td>
      <td><div>05/11/2026</div><div>JP No. 2</div></td>
      <td valign="top" nowrap="true"><div>EFile Evictions</div><div>Filed</div></td>
      <td></td>
    </tr>
    <tr>
      <td><a href="CaseDetail.aspx?CaseID=6034300">JP02-26-SC00012345</a></td>
      <td></td>
      <td><div>Some Creditor vs. Some Debtor</div></td>
      <td><div>05/12/2026</div><div>JP No. 2</div></td>
      <td valign="top" nowrap="true"><div>EFile Small Claims</div><div>Filed</div></td>
      <td></td>
    </tr>
  </tbody>
</table>
</body></html>
"""

# Results page with "too many matches" warning
SAMPLE_TOO_MANY_HTML = """
<html><body>
<div>--- The search resulted in too many matches to display. Narrow the search by entering more precise criteria. ---</div>
<table>
  <tbody>
    <tr>
      <td><a href="CaseDetail.aspx?CaseID=1">JP01-26-E00100001</a></td>
      <td></td>
      <td><div>Landlord vs. Tenant</div></td>
      <td><div>05/14/2026</div><div>JP No. 1</div></td>
      <td><div>EFile Evictions</div><div>Filed</div></td>
      <td></td>
    </tr>
  </tbody>
</table>
</body></html>
"""

# Empty results page
SAMPLE_EMPTY_HTML = """
<html><body>
<table><thead><tr><th>Case Number</th></tr></thead><tbody></tbody></table>
</body></html>
"""

# CaseDetail page — eviction with two defendants at same address
SAMPLE_DETAIL_TWO_DEFENDANTS = """
<html><body>
<div>Case No. JP01-26-E00110781</div>
<div>Bel Fossil LP vs. Jennifer Phillips,Justin Emerson AND ALL OCCUPANTS</div>
<div>Case Type: EFile Evictions</div>
<div>Date Filed: 05/14/2026</div>
<table>
  <tr><td colspan="2"><b>Party Information</b></td><td><b>Lead Attorneys</b></td></tr>
  <tr>
    <td>Defendant</td>
    <td><a href="#">Emerson, Justin</a></td>
    <td></td>
    <td valign="top" nowrap="true">
      <div>&nbsp;&nbsp;3543 Meares Dr Apt 231</div>
      <div>&nbsp;&nbsp;Fort Worth, TX 76137</div>
    </td>
  </tr>
  <tr>
    <td>Defendant</td>
    <td><a href="#">Phillips, Jennifer</a></td>
    <td></td>
    <td valign="top" nowrap="true">
      <div>&nbsp;&nbsp;3543 Meares Dr Apt 231</div>
      <div>&nbsp;&nbsp;Fort Worth, TX 76137</div>
    </td>
  </tr>
  <tr>
    <td>Plaintiff</td>
    <td>Bel Fossil LP</td>
    <td></td>
    <td valign="top">
      <div>&nbsp;&nbsp;3600 Basswood Blvd</div>
      <div>&nbsp;&nbsp;Fort Worth, TX 76137</div>
    </td>
  </tr>
</table>
<table>
  <tr><td>06/04/2026</td><td></td><td></td>
      <td>Eviction Non-Jury Trial&nbsp;(8:00 AM) (Judicial Officer&nbsp;Swearingin, Ralph, JR)</td>
  </tr>
</table>
</body></html>
"""

# CaseDetail page — single defendant, no court date yet
SAMPLE_DETAIL_SINGLE_DEFENDANT = """
<html><body>
<div>Case No. JP01-26-E00110695</div>
<table>
  <tr><td colspan="2"><b>Party Information</b></td><td><b>Lead Attorneys</b></td></tr>
  <tr>
    <td>Defendant</td>
    <td><a href="#">Ridge, Alex</a></td>
    <td></td>
    <td valign="top" nowrap="true">
      <div>&nbsp;&nbsp;4210 Fossil Creek Blvd</div>
      <div>&nbsp;&nbsp;Fort Worth, TX 76137</div>
    </td>
  </tr>
  <tr>
    <td>Plaintiff</td>
    <td>ASD HC Fossil Creek I Fee Owner LLC</td>
    <td></td>
    <td valign="top"></td>
  </tr>
</table>
</body></html>
"""

# CaseDetail page — defendant has no recognisable address
SAMPLE_DETAIL_NO_ADDRESS = """
<html><body>
<table>
  <tr>
    <td>Defendant</td>
    <td><a href="#">Smith, John</a></td>
    <td></td>
    <td valign="top"></td>
  </tr>
</table>
</body></html>
"""

SAMPLE_DETAIL_CURRENT_PARTY_TABLE = """
<html><body>
<table>
  <caption><div class="ssCaseDetailSectionTitle">Party Information</div></caption>
  <tbody>
    <tr>
      <td colspan="4"></td><th class="ssTableHeader" id="PIc5">Lead Attorneys</th>
    </tr>
    <tr>
      <th class="ssTableHeader" valign="top" rowspan="2" id="PIr01">Defendant</th>
      <th class="ssTableHeader" valign="top" id="PIr11">Bowles, Stephanie</th>
      <td rowspan="2"></td><td rowspan="2" valign="top"></td><td rowspan="2"></td>
    </tr>
    <tr>
      <td valign="top">&nbsp;&nbsp;6728 park vista blvd, 2804<br>&nbsp;&nbsp;Watauga, TX 76137<br></td>
    </tr>
    <tr>
      <th class="ssTableHeader" valign="top" rowspan="2" id="PIr02">Plaintiff</th>
      <th class="ssTableHeader" valign="top" id="PIr12">PARK VISTA OTM HH LP</th>
    </tr>
    <tr>
      <td valign="top">&nbsp;&nbsp;2901 Dallas Parkway, Ste 250<br>&nbsp;&nbsp;Plano, TX 75093<br></td>
    </tr>
  </tbody>
</table>
<table>
  <tr><td>06/11/2026</td><td>Eviction Non-Jury Trial</td></tr>
</table>
</body></html>
"""


# ---------------------------------------------------------------------------
# _clean_tenant
# ---------------------------------------------------------------------------

class TestCleanTenant:
    def test_strips_and_all_occupants(self):
        assert _clean_tenant("Jennifer Phillips AND ALL OCCUPANTS") == "Jennifer Phillips"

    def test_strips_and_all_other_occupants(self):
        assert _clean_tenant("Alex Ridge AND ALL OTHER OCCUPANTS") == "Alex Ridge"

    def test_strips_et_al(self):
        assert _clean_tenant("Martinez, Rodolfo et al.") == "Martinez, Rodolfo"

    def test_takes_first_of_comma_list(self):
        # "Jennifer Phillips,Justin Emerson AND ALL OCCUPANTS" → first = "Jennifer Phillips"
        result = _clean_tenant("Jennifer Phillips,Justin Emerson AND ALL OCCUPANTS")
        assert result == "Jennifer Phillips"

    def test_single_name_unchanged(self):
        assert _clean_tenant("VINCENT LEE MCGEE") == "VINCENT LEE MCGEE"

    def test_empty_string_returns_unknown(self):
        assert _clean_tenant("") == "Unknown"

    def test_case_insensitive(self):
        assert _clean_tenant("Doe, Jane and all occupants") == "Doe, Jane"


# ---------------------------------------------------------------------------
# _parse_style
# ---------------------------------------------------------------------------

class TestParseStyle:
    def test_standard_split(self):
        landlord, tenant = _parse_style(
            "Bel Fossil LP vs. Jennifer Phillips,Justin Emerson AND ALL OCCUPANTS"
        )
        assert landlord == "Bel Fossil LP"
        assert tenant == "Jennifer Phillips"

    def test_all_caps(self):
        landlord, tenant = _parse_style(
            "SPRING LAKE VILLAGE LLC vs. VINCENT LEE MCGEE AND ALL OCCUPANTS"
        )
        assert landlord == "SPRING LAKE VILLAGE LLC"
        assert tenant == "VINCENT LEE MCGEE"

    def test_no_vs_returns_unknowns(self):
        landlord, tenant = _parse_style("No separator here")
        assert landlord == "Unknown"
        assert tenant == "Unknown"

    def test_empty_string(self):
        landlord, tenant = _parse_style("")
        assert landlord == "Unknown"
        assert tenant == "Unknown"

    def test_landlord_stripped(self):
        landlord, _ = _parse_style("  Big Landlord LLC   vs.  Tenant Name  ")
        assert landlord == "Big Landlord LLC"


# ---------------------------------------------------------------------------
# _parse_results_page
# ---------------------------------------------------------------------------

class TestParseResultsPage:
    def test_returns_only_eviction_rows(self):
        rows = _parse_results_page(SAMPLE_RESULTS_HTML)
        for r in rows:
            assert r["case_number"].startswith("JP")
            # All returned rows must be eviction cases (E prefix)
        case_numbers = [r["case_number"] for r in rows]
        assert "JP01-26-DC00038820" not in case_numbers  # debt claim excluded
        assert "JP02-26-SC00012345" not in case_numbers  # small claim excluded

    def test_correct_eviction_count(self):
        rows = _parse_results_page(SAMPLE_RESULTS_HTML)
        assert len(rows) == 3

    def test_case_id_extracted(self):
        rows = _parse_results_page(SAMPLE_RESULTS_HTML)
        ids = {r["case_id"] for r in rows}
        assert "6034068" in ids
        assert "6034100" in ids
        assert "6034200" in ids

    def test_case_number_extracted(self):
        rows = _parse_results_page(SAMPLE_RESULTS_HTML)
        nums = {r["case_number"] for r in rows}
        assert "JP01-26-E00110781" in nums
        assert "JP01-26-E00110795" in nums

    def test_landlord_parsed(self):
        rows = _parse_results_page(SAMPLE_RESULTS_HTML)
        by_case = {r["case_number"]: r for r in rows}
        assert by_case["JP01-26-E00110781"]["landlord"] == "Bel Fossil LP"
        assert by_case["JP01-26-E00110795"]["landlord"] == "ASD HC Fossil Creek I Fee Owner LLC"

    def test_tenant_parsed_and_cleaned(self):
        rows = _parse_results_page(SAMPLE_RESULTS_HTML)
        by_case = {r["case_number"]: r for r in rows}
        assert by_case["JP01-26-E00110781"]["tenant"] == "Jennifer Phillips"
        assert by_case["JP01-26-E00110795"]["tenant"] == "Alex Ridge"
        assert by_case["JP02-26-E00110900"]["tenant"] == "VINCENT LEE MCGEE"

    def test_filing_date_parsed(self):
        rows = _parse_results_page(SAMPLE_RESULTS_HTML)
        by_case = {r["case_number"]: r for r in rows}
        assert by_case["JP01-26-E00110781"]["filing_date"] == date(2026, 5, 14)
        assert by_case["JP01-26-E00110795"]["filing_date"] == date(2026, 5, 8)

    def test_court_location_parsed(self):
        rows = _parse_results_page(SAMPLE_RESULTS_HTML)
        by_case = {r["case_number"]: r for r in rows}
        assert "JP No. 1" in by_case["JP01-26-E00110781"]["court_location"]

    def test_empty_html_returns_empty(self):
        rows = _parse_results_page(SAMPLE_EMPTY_HTML)
        assert rows == []

    def test_too_many_message_does_not_affect_parse(self):
        # _parse_results_page itself just parses whatever is there;
        # the caller checks for "too many matches" separately.
        rows = _parse_results_page(SAMPLE_TOO_MANY_HTML)
        assert len(rows) == 1

    def test_no_eviction_rows_returns_empty(self):
        html = """
        <table>
          <tr>
            <td><a href="CaseDetail.aspx?CaseID=1">JP01-26-DC00001</a></td>
            <td></td><td>Bank vs. Person</td>
            <td>05/14/2026</td>
            <td>EFile Debt Claims</td><td></td>
          </tr>
        </table>
        """
        assert _parse_results_page(html) == []


# ---------------------------------------------------------------------------
# _parse_case_detail
# ---------------------------------------------------------------------------

class TestParseCaseDetail:
    def test_extracts_address_from_first_defendant(self):
        result = _parse_case_detail(SAMPLE_DETAIL_TWO_DEFENDANTS)
        assert "3543 Meares Dr Apt 231" in result["property_address"]
        assert "Fort Worth" in result["property_address"]

    def test_extracts_court_date(self):
        result = _parse_case_detail(SAMPLE_DETAIL_TWO_DEFENDANTS)
        assert result["court_date"] == date(2026, 6, 4)

    def test_single_defendant_address(self):
        result = _parse_case_detail(SAMPLE_DETAIL_SINGLE_DEFENDANT)
        assert "4210 Fossil Creek Blvd" in result["property_address"]
        assert "Fort Worth" in result["property_address"]

    def test_no_court_date_returns_none(self):
        result = _parse_case_detail(SAMPLE_DETAIL_SINGLE_DEFENDANT)
        assert result["court_date"] is None

    def test_no_address_returns_unknown(self):
        result = _parse_case_detail(SAMPLE_DETAIL_NO_ADDRESS)
        assert result["property_address"] == "Unknown"

    def test_no_address_no_court_date(self):
        result = _parse_case_detail("<html><body>nothing</body></html>")
        assert result["property_address"] == "Unknown"
        assert result["court_date"] is None

    def test_current_party_table_extracts_defendant_address(self):
        result = _parse_case_detail(SAMPLE_DETAIL_CURRENT_PARTY_TABLE)
        assert result["property_address"] == "6728 park vista blvd, 2804, Watauga, TX 76137"
        assert "2901 Dallas Parkway" not in result["property_address"]

    def test_plaintiff_address_not_used(self):
        # Plaintiff address should not be returned — only defendant's
        result = _parse_case_detail(SAMPLE_DETAIL_TWO_DEFENDANTS)
        # Plaintiff address is "3600 Basswood Blvd" — should NOT appear
        assert "3600 Basswood Blvd" not in result["property_address"]


# ---------------------------------------------------------------------------
# TarrantCountyJPScraper unit (no network)
# ---------------------------------------------------------------------------

class TestTarrantCountyJPScraperUnit:
    def test_default_lookback_days(self):
        scraper = TarrantCountyJPScraper()
        assert scraper.lookback_days == 2

    def test_custom_lookback_days(self):
        scraper = TarrantCountyJPScraper(lookback_days=7)
        assert scraper.lookback_days == 7

    def test_last_error_initially_none(self):
        scraper = TarrantCountyJPScraper()
        assert scraper.last_error is None

    def test_max_cases_default_none(self):
        scraper = TarrantCountyJPScraper()
        assert scraper.max_cases is None

    def test_max_cases_custom(self):
        scraper = TarrantCountyJPScraper(max_cases=25)
        assert scraper.max_cases == 25
