from __future__ import annotations

from datetime import date

from scrapers.ohio.butler import (
    ButlerCountyAreaCourtScraper,
    _fetch_defendant_address,
    _get_csrf_token,
    _parse_address_cell,
    _parse_search_results,
    _strip_occupant_suffix,
)


# ---------------------------------------------------------------------------
# HTML fixtures — modelled on real CaseLook (Henschen) HTML structure.
# Confirmed case data: CVG2600238, Entsminger Donald,
# defendant address: 5425 Stillwell Beckett Rd, Oxford OH 45056
# Portal: https://docket.bcohio.gov
# ---------------------------------------------------------------------------

SAMPLE_SEARCH_HTML = """
<html><body>
<form>
  <input type="hidden" name="_token" value="test-csrf-token-abc123">
</form>
<table class="table table-striped table-hover">
  <thead>
    <tr>
      <th>Case Number</th>
      <th>Plaintiff</th>
      <th>Defendant</th>
      <th>Hearing Date</th>
      <th>Cause of Action</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><a href="/record/2001/tok111">CVG2600238</a></td>
      <td>ENTSMINGER, DONALD</td>
      <td>Smith, James</td>
      <td>06/10/2026</td>
      <td>EVICTIONS</td>
    </tr>
    <tr>
      <td><a href="/record/2002/tok222">CVG2600239</a></td>
      <td>ACORN PROPERTIES LLC</td>
      <td>Doe, Jane et al</td>
      <td>06/10/2026</td>
      <td>EVICTIONS</td>
    </tr>
    <tr>
      <td><a href="/record/2003/tok333">CVI2600100</a></td>
      <td>BUTLER BANK</td>
      <td>Jones, Dave</td>
      <td>06/10/2026</td>
      <td>CIVIL CLAIM</td>
    </tr>
  </tbody>
</table>
</body></html>
"""

SAMPLE_SEARCH_HTML_DUPLICATE = """
<html><body>
<form>
  <input type="hidden" name="_token" value="test-csrf-token-dup">
</form>
<table class="table table-striped table-hover">
  <tbody>
    <tr>
      <td><a href="/record/2001/tok111">CVG2600238</a></td>
      <td>ENTSMINGER, DONALD</td>
      <td>Smith, James</td>
      <td>06/10/2026</td>
      <td>EVICTIONS</td>
    </tr>
    <tr>
      <td><a href="/record/2001/tok111">CVG2600238</a></td>
      <td>ENTSMINGER, DONALD</td>
      <td>Smith, James</td>
      <td>06/10/2026</td>
      <td>EVICTIONS</td>
    </tr>
  </tbody>
</table>
</body></html>
"""

SAMPLE_DETAIL_HTML = """
<html><body>
<form>
  <input type="hidden" name="_token" value="test-csrf-token-detail">
</form>
<h2>Case CVG2600238</h2>
<table class="table">
  <thead>
    <tr><th>Type</th><th>Name</th><th>Address</th></tr>
  </thead>
  <tbody>
    <tr>
      <td>Plaintiff</td>
      <td>ENTSMINGER, DONALD</td>
      <td>100 Main St<br/>Oxford, OH 45056</td>
    </tr>
    <tr>
      <td>Defendant</td>
      <td>Smith, James</td>
      <td>5425 Stillwell Beckett Rd<br/>Oxford, OH 45056</td>
    </tr>
  </tbody>
</table>
</body></html>
"""

SAMPLE_DETAIL_HTML_NO_ADDRESS = """
<html><body>
<table class="table">
  <thead>
    <tr><th>Type</th><th>Name</th><th>Address</th></tr>
  </thead>
  <tbody>
    <tr>
      <td>Plaintiff</td>
      <td>SOME LANDLORD LLC</td>
      <td></td>
    </tr>
    <tr>
      <td>Defendant</td>
      <td>Doe, Jane</td>
      <td></td>
    </tr>
  </tbody>
</table>
</body></html>
"""

SEARCH_DATE = date(2026, 6, 4)


# ---------------------------------------------------------------------------
# _strip_occupant_suffix
# ---------------------------------------------------------------------------

def test_strip_occupant_suffix_removes_et_al():
    assert _strip_occupant_suffix("Doe, Jane et al") == "Doe, Jane"


def test_strip_occupant_suffix_removes_et_al_with_period():
    assert _strip_occupant_suffix("JOHN DOE et al.") == "JOHN DOE"


def test_strip_occupant_suffix_removes_and_all_other_occupants():
    assert _strip_occupant_suffix("JANE SMITH AND ALL OTHER OCCUPANTS") == "JANE SMITH"


def test_strip_occupant_suffix_removes_and_all_occupants():
    assert _strip_occupant_suffix("MARY JONES AND ALL OCCUPANTS") == "MARY JONES"


def test_strip_occupant_suffix_removes_and_all_others():
    assert _strip_occupant_suffix("BROWN, KEVIN AND ALL OTHERS") == "BROWN, KEVIN"


def test_strip_occupant_suffix_leaves_plain_names_unchanged():
    assert _strip_occupant_suffix("Smith, James") == "Smith, James"


# ---------------------------------------------------------------------------
# _get_csrf_token
# ---------------------------------------------------------------------------

def test_get_csrf_token_finds_hidden_input():
    html = '<form><input type="hidden" name="_token" value="abc123xyz"></form>'
    assert _get_csrf_token(html) == "abc123xyz"


def test_get_csrf_token_falls_back_to_meta_tag():
    html = '<meta name="csrf-token" content="meta-token-456">'
    assert _get_csrf_token(html) == "meta-token-456"


def test_get_csrf_token_returns_none_when_absent():
    html = "<html><body><p>No token here</p></body></html>"
    assert _get_csrf_token(html) is None


def test_get_csrf_token_extracts_from_sample_search_html():
    assert _get_csrf_token(SAMPLE_SEARCH_HTML) == "test-csrf-token-abc123"


# ---------------------------------------------------------------------------
# _parse_address_cell
# ---------------------------------------------------------------------------

class TestParseAddressCell:
    def test_standard_two_line_address(self):
        from bs4 import BeautifulSoup
        html = "<td>5425 Stillwell Beckett Rd<br/>Oxford, OH 45056</td>"
        td = BeautifulSoup(html, "html.parser").find("td")
        assert _parse_address_cell(td) == "5425 Stillwell Beckett Rd, Oxford, OH 45056"

    def test_address_with_apartment(self):
        from bs4 import BeautifulSoup
        html = "<td>123 Main St Apt 4<br/>Hamilton, OH 45011</td>"
        td = BeautifulSoup(html, "html.parser").find("td")
        assert _parse_address_cell(td) == "123 Main St Apt 4, Hamilton, OH 45011"

    def test_nine_digit_zip_truncated(self):
        from bs4 import BeautifulSoup
        html = "<td>200 Oak Ave<br/>Oxford, OH 450560001</td>"
        td = BeautifulSoup(html, "html.parser").find("td")
        result = _parse_address_cell(td)
        assert "45056" in result
        assert "450560001" not in result

    def test_empty_cell_returns_empty(self):
        from bs4 import BeautifulSoup
        html = "<td></td>"
        td = BeautifulSoup(html, "html.parser").find("td")
        assert _parse_address_cell(td) == ""


# ---------------------------------------------------------------------------
# _parse_search_results
# ---------------------------------------------------------------------------

def test_parse_search_results_returns_only_cvg_cases():
    filings = _parse_search_results(SAMPLE_SEARCH_HTML, search_date=SEARCH_DATE)
    assert len(filings) == 2
    assert all(f.case_number.startswith("CVG") for f in filings)


def test_parse_search_results_maps_case_number():
    filings = _parse_search_results(SAMPLE_SEARCH_HTML, search_date=SEARCH_DATE)
    assert filings[0].case_number == "CVG2600238"
    assert filings[1].case_number == "CVG2600239"


def test_parse_search_results_maps_landlord_and_tenant():
    filings = _parse_search_results(SAMPLE_SEARCH_HTML, search_date=SEARCH_DATE)
    assert filings[0].landlord_name == "ENTSMINGER, DONALD"
    assert filings[0].tenant_name == "Smith, James"


def test_parse_search_results_strips_occupant_suffix_from_tenant():
    filings = _parse_search_results(SAMPLE_SEARCH_HTML, search_date=SEARCH_DATE)
    assert filings[1].tenant_name == "Doe, Jane"


def test_parse_search_results_sets_filing_and_court_date_to_search_date():
    filings = _parse_search_results(SAMPLE_SEARCH_HTML, search_date=SEARCH_DATE)
    assert filings[0].filing_date == SEARCH_DATE
    assert filings[0].court_date == SEARCH_DATE


def test_parse_search_results_sets_county_state_notice_type():
    filings = _parse_search_results(SAMPLE_SEARCH_HTML, search_date=SEARCH_DATE)
    assert filings[0].state == "OH"
    assert filings[0].county == "Butler"
    assert filings[0].notice_type == "Eviction"


def test_parse_search_results_placeholder_address():
    filings = _parse_search_results(SAMPLE_SEARCH_HTML, search_date=SEARCH_DATE)
    assert filings[0].property_address == "Unknown"


def test_parse_search_results_source_url_points_to_record():
    filings = _parse_search_results(SAMPLE_SEARCH_HTML, search_date=SEARCH_DATE)
    assert "/record/2001/tok111" in filings[0].source_url


def test_parse_search_results_returns_empty_for_blank_page():
    filings = _parse_search_results(
        "<html><body><p>No results.</p></body></html>",
        search_date=SEARCH_DATE,
    )
    assert filings == []


def test_parse_search_results_placeholder_tenant_falls_back_to_unknown(monkeypatch):
    """When clean_tenant_name returns '' (placeholder/junk name), fall back to 'Unknown'
    rather than tenant_raw — prevents re-injecting occupant suffixes into the pipeline."""
    import scrapers.ohio.butler as mod
    monkeypatch.setattr(mod, "clean_tenant_name", lambda _: "")

    filings = _parse_search_results(SAMPLE_SEARCH_HTML, search_date=SEARCH_DATE)
    assert len(filings) > 0
    assert all(f.tenant_name == "Unknown" for f in filings)


# ---------------------------------------------------------------------------
# _fetch_defendant_address
# ---------------------------------------------------------------------------

class TestFetchDefendantAddress:
    def test_returns_defendant_address_from_table(self):
        from unittest.mock import MagicMock
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = SAMPLE_DETAIL_HTML
        mock_session.get.return_value = mock_resp

        result = _fetch_defendant_address(mock_session, "https://docket.bcohio.gov/record/2001/tok111")
        assert result == "5425 Stillwell Beckett Rd, Oxford, OH 45056"

    def test_returns_none_when_address_cell_empty(self):
        from unittest.mock import MagicMock
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = SAMPLE_DETAIL_HTML_NO_ADDRESS
        mock_session.get.return_value = mock_resp

        result = _fetch_defendant_address(mock_session, "https://docket.bcohio.gov/record/2002/tok222")
        assert result is None

    def test_returns_none_on_http_error(self):
        from unittest.mock import MagicMock
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_session.get.return_value = mock_resp

        result = _fetch_defendant_address(mock_session, "https://docket.bcohio.gov/record/9999/bad")
        assert result is None

    def test_returns_none_on_exception(self):
        from unittest.mock import MagicMock
        mock_session = MagicMock()
        mock_session.get.side_effect = Exception("connection timeout")

        result = _fetch_defendant_address(mock_session, "https://docket.bcohio.gov/record/2001/tok111")
        assert result is None


# ---------------------------------------------------------------------------
# ButlerCountyAreaCourtScraper — error handling and deduplication
# ---------------------------------------------------------------------------

def test_scraper_records_last_error_when_session_fails(monkeypatch):
    scraper = ButlerCountyAreaCourtScraper(lookback_days=2, lookahead_days=30)

    monkeypatch.setattr(scraper, "_ensure_session", lambda: False)

    filings = scraper.scrape()

    assert filings == []
    assert scraper.last_error is not None


def test_scraper_records_last_error_when_search_fails(monkeypatch):
    scraper = ButlerCountyAreaCourtScraper(lookback_days=2, lookahead_days=30)

    monkeypatch.setattr(scraper, "_ensure_session", lambda: True)
    scraper._session_ready = True

    def fail_post_search(_start, _end):
        raise ConnectionResetError("connection reset")

    monkeypatch.setattr(scraper, "_post_search", fail_post_search)

    filings = scraper.scrape()

    assert filings == []
    assert "connection reset" in scraper.last_error


def test_scraper_dedupes_duplicate_cases_in_results(monkeypatch):
    """Butler does a single date-range POST; dedup guards against duplicate rows."""
    scraper = ButlerCountyAreaCourtScraper(lookback_days=2, lookahead_days=30)

    monkeypatch.setattr(scraper, "_ensure_session", lambda: True)
    scraper._session_ready = True

    monkeypatch.setattr(scraper, "_post_search", lambda _s, _e: SAMPLE_SEARCH_HTML_DUPLICATE)
    monkeypatch.setattr(
        "scrapers.ohio.butler._fetch_defendant_address", lambda _session, _url: None
    )

    filings = scraper.scrape()

    case_numbers = [f.case_number for f in filings]
    assert len(case_numbers) == len(set(case_numbers)), "Duplicate case numbers found"
    assert len(filings) == 1


def test_scraper_upgrades_placeholder_address_when_detail_succeeds(monkeypatch):
    scraper = ButlerCountyAreaCourtScraper(lookback_days=2, lookahead_days=30)

    monkeypatch.setattr(scraper, "_ensure_session", lambda: True)
    scraper._session_ready = True

    monkeypatch.setattr(scraper, "_post_search", lambda _s, _e: SAMPLE_SEARCH_HTML)
    monkeypatch.setattr(
        "scrapers.ohio.butler._fetch_defendant_address",
        lambda _session, _url: "5425 Stillwell Beckett Rd, Oxford, OH 45056",
    )

    filings = scraper.scrape()

    assert len(filings) > 0
    assert filings[0].property_address == "5425 Stillwell Beckett Rd, Oxford, OH 45056"


def test_scraper_uses_unknown_fallback_when_detail_returns_none(monkeypatch):
    scraper = ButlerCountyAreaCourtScraper(lookback_days=2, lookahead_days=30)

    monkeypatch.setattr(scraper, "_ensure_session", lambda: True)
    scraper._session_ready = True

    monkeypatch.setattr(scraper, "_post_search", lambda _s, _e: SAMPLE_SEARCH_HTML)
    monkeypatch.setattr(
        "scrapers.ohio.butler._fetch_defendant_address",
        lambda _session, _url: None,
    )

    filings = scraper.scrape()

    assert len(filings) > 0
    assert filings[0].property_address == "Unknown"


def test_scraper_clears_last_error_when_filings_returned(monkeypatch):
    scraper = ButlerCountyAreaCourtScraper(lookback_days=2, lookahead_days=30)

    monkeypatch.setattr(scraper, "_ensure_session", lambda: True)
    scraper._session_ready = True

    monkeypatch.setattr(scraper, "_post_search", lambda _s, _e: SAMPLE_SEARCH_HTML)
    monkeypatch.setattr(
        "scrapers.ohio.butler._fetch_defendant_address",
        lambda _session, _url: None,
    )

    filings = scraper.scrape()

    assert len(filings) > 0
    assert scraper.last_error is None
