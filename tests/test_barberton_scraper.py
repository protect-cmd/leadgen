from __future__ import annotations

from datetime import date

from scrapers.ohio.barberton import (
    BarbertonMunicipalScraper,
    _fetch_defendant_address,
    _get_csrf_token,
    _parse_address_cell,
    _parse_search_results,
    _strip_occupant_suffix,
)


# ---------------------------------------------------------------------------
# HTML fixtures — modelled on real CaseLook (Henschen) HTML structure.
# Confirmed case data: CVG2601199, Brown Rebecca, 87 Helen Street, Barberton OH 44203
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
      <th>Filed Date</th>
      <th>Cause of Action</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><a href="/record/1001/tok111">CVG2601199</a></td>
      <td>M&amp;C MHP LLC</td>
      <td>Brown, Rebecca</td>
      <td>05/01/2026</td>
      <td>EVICTION</td>
    </tr>
    <tr>
      <td><a href="/record/1002/tok222">CVG2601200</a></td>
      <td>WALNUT HILL PROPERTIES</td>
      <td>Walker, Robert et al</td>
      <td>05/01/2026</td>
      <td>EVICTION</td>
    </tr>
    <tr>
      <td><a href="/record/1003/tok333">CVI2600500</a></td>
      <td>ACME CORP</td>
      <td>Jones, Dave</td>
      <td>05/01/2026</td>
      <td>CIVIL INJURY</td>
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
<h2>Case CVG2601199</h2>
<p>File Date: 05/01/2026</p>
<table class="table">
  <thead>
    <tr><th>Type</th><th>Name</th><th>Address</th></tr>
  </thead>
  <tbody>
    <tr>
      <td>Plaintiff</td>
      <td>M&amp;C MHP LLC</td>
      <td>5854 Cleveland Road<br/>Wooster, OH 44691</td>
    </tr>
    <tr>
      <td>Defendant</td>
      <td>Brown, Rebecca</td>
      <td>87 Helen Street<br/>Barberton, OH 44203</td>
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

SEARCH_DATE = date(2026, 5, 1)


# ---------------------------------------------------------------------------
# _strip_occupant_suffix
# ---------------------------------------------------------------------------

def test_strip_occupant_suffix_removes_et_al():
    assert _strip_occupant_suffix("Walker, Robert et al") == "Walker, Robert"


def test_strip_occupant_suffix_removes_et_al_with_period():
    assert _strip_occupant_suffix("JOHN DOE et al.") == "JOHN DOE"


def test_strip_occupant_suffix_removes_and_all_other_occupants():
    assert _strip_occupant_suffix("JANE SMITH AND ALL OTHER OCCUPANTS") == "JANE SMITH"


def test_strip_occupant_suffix_removes_and_all_occupants():
    assert _strip_occupant_suffix("MARY JONES AND ALL OCCUPANTS") == "MARY JONES"


def test_strip_occupant_suffix_leaves_plain_names_unchanged():
    assert _strip_occupant_suffix("Brown, Rebecca") == "Brown, Rebecca"


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
        html = "<td>87 Helen Street<br/>Barberton, OH 44203</td>"
        td = BeautifulSoup(html, "html.parser").find("td")
        assert _parse_address_cell(td) == "87 Helen Street, Barberton, OH 44203"

    def test_address_with_apartment(self):
        from bs4 import BeautifulSoup
        html = "<td>123 Main St Apt 4<br/>Barberton, OH 44203</td>"
        td = BeautifulSoup(html, "html.parser").find("td")
        assert _parse_address_cell(td) == "123 Main St Apt 4, Barberton, OH 44203"

    def test_nine_digit_zip_truncated(self):
        from bs4 import BeautifulSoup
        html = "<td>200 Oak Ave<br/>Barberton, OH 442030001</td>"
        td = BeautifulSoup(html, "html.parser").find("td")
        result = _parse_address_cell(td)
        assert "44203" in result
        assert "442030001" not in result

    def test_empty_cell_returns_empty(self):
        from bs4 import BeautifulSoup
        html = "<td></td>"
        td = BeautifulSoup(html, "html.parser").find("td")
        assert _parse_address_cell(td) == ""


# ---------------------------------------------------------------------------
# _parse_search_results
# ---------------------------------------------------------------------------

def test_parse_search_results_returns_only_cvg_cases():
    filings = _parse_search_results(
        SAMPLE_SEARCH_HTML, search_date=SEARCH_DATE
    )
    # CVI case should be filtered out
    assert len(filings) == 2
    assert all(f.case_number.startswith("CVG") for f in filings)


def test_parse_search_results_maps_case_number():
    filings = _parse_search_results(
        SAMPLE_SEARCH_HTML, search_date=SEARCH_DATE
    )
    assert filings[0].case_number == "CVG2601199"
    assert filings[1].case_number == "CVG2601200"


def test_parse_search_results_maps_landlord_and_tenant():
    filings = _parse_search_results(
        SAMPLE_SEARCH_HTML, search_date=SEARCH_DATE
    )
    assert filings[0].landlord_name == "M&C MHP LLC"
    assert filings[0].tenant_name == "Brown, Rebecca"


def test_parse_search_results_strips_occupant_suffix_from_tenant():
    filings = _parse_search_results(
        SAMPLE_SEARCH_HTML, search_date=SEARCH_DATE
    )
    assert filings[1].tenant_name == "Walker, Robert"


def test_parse_search_results_sets_filing_date_to_search_date():
    filings = _parse_search_results(
        SAMPLE_SEARCH_HTML, search_date=SEARCH_DATE
    )
    assert filings[0].filing_date == SEARCH_DATE


def test_parse_search_results_sets_county_state_notice_type():
    filings = _parse_search_results(
        SAMPLE_SEARCH_HTML, search_date=SEARCH_DATE
    )
    assert filings[0].state == "OH"
    assert filings[0].county == "Summit"
    assert filings[0].notice_type == "Eviction"


def test_parse_search_results_placeholder_address():
    filings = _parse_search_results(
        SAMPLE_SEARCH_HTML, search_date=SEARCH_DATE
    )
    # All stubs get "Unknown" placeholder — upgraded later by _fetch_defendant_address
    assert filings[0].property_address == "Unknown"


def test_parse_search_results_source_url_points_to_record():
    filings = _parse_search_results(
        SAMPLE_SEARCH_HTML, search_date=SEARCH_DATE
    )
    assert "/record/1001/tok111" in filings[0].source_url


def test_parse_search_results_returns_empty_for_blank_page():
    filings = _parse_search_results(
        "<html><body><p>No results.</p></body></html>",
        search_date=SEARCH_DATE,
    )
    assert filings == []


def test_parse_search_results_placeholder_tenant_falls_back_to_unknown(monkeypatch):
    """When clean_tenant_name returns '' (placeholder/junk name), fall back to 'Unknown'
    rather than tenant_raw — prevents re-injecting occupant suffixes into the pipeline.
    Mirrors the fix applied to Montgomery (PR #10 follow-up)."""
    import scrapers.ohio.barberton as mod
    monkeypatch.setattr(mod, "clean_tenant_name", lambda _: "")

    filings = _parse_search_results(
        SAMPLE_SEARCH_HTML, search_date=SEARCH_DATE
    )
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

        result = _fetch_defendant_address(mock_session, "https://example.com/record/1001/tok111")
        assert result == "87 Helen Street, Barberton, OH 44203"

    def test_returns_none_when_address_cell_empty(self):
        from unittest.mock import MagicMock
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = SAMPLE_DETAIL_HTML_NO_ADDRESS
        mock_session.get.return_value = mock_resp

        result = _fetch_defendant_address(mock_session, "https://example.com/record/1002/tok222")
        assert result is None

    def test_returns_none_on_http_error(self):
        from unittest.mock import MagicMock
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_session.get.return_value = mock_resp

        result = _fetch_defendant_address(mock_session, "https://example.com/record/9999/bad")
        assert result is None

    def test_returns_none_on_exception(self):
        from unittest.mock import MagicMock
        mock_session = MagicMock()
        mock_session.get.side_effect = Exception("connection timeout")

        result = _fetch_defendant_address(mock_session, "https://example.com/record/1001/tok111")
        assert result is None


# ---------------------------------------------------------------------------
# BarbertonMunicipalScraper — error handling and deduplication
# ---------------------------------------------------------------------------

def test_scraper_records_last_error_when_session_fails(monkeypatch):
    scraper = BarbertonMunicipalScraper(lookback_days=2)

    def fail_ensure_session():
        return False

    monkeypatch.setattr(scraper, "_ensure_session", fail_ensure_session)

    filings = scraper.scrape()

    assert filings == []
    assert scraper.last_error is not None


def test_scraper_records_last_error_when_search_fails(monkeypatch):
    scraper = BarbertonMunicipalScraper(lookback_days=0)

    monkeypatch.setattr(scraper, "_ensure_session", lambda: True)
    scraper._session_ready = True

    def fail_post_search(_date):
        raise ConnectionResetError("connection reset")

    monkeypatch.setattr(scraper, "_post_search", fail_post_search)

    filings = scraper.scrape()

    assert filings == []
    assert "connection reset" in scraper.last_error


def test_scraper_dedupes_same_case_across_dates(monkeypatch):
    scraper = BarbertonMunicipalScraper(lookback_days=2)

    monkeypatch.setattr(scraper, "_ensure_session", lambda: True)
    scraper._session_ready = True

    def fake_post_search(_date):
        return SAMPLE_SEARCH_HTML

    def fake_fetch_address(_session, _url):
        return None

    monkeypatch.setattr(scraper, "_post_search", fake_post_search)
    monkeypatch.setattr(
        "scrapers.ohio.barberton._fetch_defendant_address", fake_fetch_address
    )

    filings = scraper.scrape()

    case_numbers = [f.case_number for f in filings]
    assert len(case_numbers) == len(set(case_numbers)), "Duplicate case numbers found"


def test_scraper_upgrades_placeholder_address_when_detail_succeeds(monkeypatch):
    scraper = BarbertonMunicipalScraper(lookback_days=0)

    monkeypatch.setattr(scraper, "_ensure_session", lambda: True)
    scraper._session_ready = True

    monkeypatch.setattr(scraper, "_post_search", lambda _d: SAMPLE_SEARCH_HTML)
    monkeypatch.setattr(
        "scrapers.ohio.barberton._fetch_defendant_address",
        lambda _session, _url: "87 Helen Street, Barberton, OH 44203",
    )

    filings = scraper.scrape()

    assert len(filings) > 0
    assert filings[0].property_address == "87 Helen Street, Barberton, OH 44203"


def test_scraper_uses_unknown_fallback_when_detail_returns_none(monkeypatch):
    scraper = BarbertonMunicipalScraper(lookback_days=0)

    monkeypatch.setattr(scraper, "_ensure_session", lambda: True)
    scraper._session_ready = True

    monkeypatch.setattr(scraper, "_post_search", lambda _d: SAMPLE_SEARCH_HTML)
    monkeypatch.setattr(
        "scrapers.ohio.barberton._fetch_defendant_address",
        lambda _session, _url: None,
    )

    filings = scraper.scrape()

    assert len(filings) > 0
    assert filings[0].property_address == "Unknown"
