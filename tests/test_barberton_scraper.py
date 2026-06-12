from __future__ import annotations

from datetime import date

from scrapers.ohio.barberton import (
    BarbertonMunicipalScraper,
    _fetch_case_detail,
    _get_csrf_token,
    _parse_search_results,
    _strip_occupant_suffix,
)


# ---------------------------------------------------------------------------
# HTML fixtures — modelled on real CaseLook (Henschen & Associates) HTML.
# Portal uses Bootstrap card layout, not tables.
# Confirmed structure from Barberton Municipal Court (COURT_ID 7721).
# ---------------------------------------------------------------------------

SAMPLE_SEARCH_HTML = """
<html><body>
<form>
  <input type="hidden" name="_token" value="test-csrf-token-abc123">
</form>

<div class="card card--results-case">
  <div class="card-header">
    <div class="row">
      <div class="col-8"><h4>1 CVG2601199</h4></div>
      <div class="col-4">
        <a href="https://caselook.barbertonclerkofcourt.com/record/7721/tok111"
           title="Case information"><i class="fas fa-info-circle"></i></a>
      </div>
    </div>
  </div>
  <div class="card-body">
    <label>Concerning:</label> Brown, Rebecca et al
    <label>Filed:</label> 05/01/2026
  </div>
</div>

<div class="card card--results-case">
  <div class="card-header">
    <div class="row">
      <div class="col-8"><h4>2 CVG2601200</h4></div>
      <div class="col-4">
        <a href="https://caselook.barbertonclerkofcourt.com/record/7721/tok222"
           title="Case information"><i class="fas fa-info-circle"></i></a>
      </div>
    </div>
  </div>
  <div class="card-body">
    <label>Concerning:</label> Walker, Robert et al
    <label>Filed:</label> 05/01/2026
  </div>
</div>

<!-- Non-CVG case — should be filtered out -->
<div class="card card--results-case">
  <div class="card-header">
    <div class="row">
      <div class="col-8"><h4>3 CVI2600500</h4></div>
      <div class="col-4">
        <a href="https://caselook.barbertonclerkofcourt.com/record/7721/tok333"
           title="Case information"><i class="fas fa-info-circle"></i></a>
      </div>
    </div>
  </div>
  <div class="card-body">
    <label>Concerning:</label> Jones, Dave
  </div>
</div>

</body></html>
"""

# Detail page HTML mirrors real portal: card--parties-MV cards, label-next-sibling pattern.
# Note the deliberate "City/Sate/ZIP:" typo — that's what the real portal returns.
SAMPLE_DETAIL_HTML = """
<html><body>
<div class="card card--parties-MV">
  <div class="card-header"><h4>Plaintiff</h4></div>
  <div class="card-body">
    <label>Plaintiff 1:</label> M&amp;C MHP LLC
    <label>Address:</label> 5854 Cleveland Road
    <label>City/State/ZIP:</label> Wooster, OH 44691
  </div>
</div>
<div class="card card--parties-MV">
  <div class="card-header"><h4>Defendants</h4></div>
  <div class="card-body">
    <label>Defendant 1:</label> Brown, Rebecca
    <label>Address:</label> 87 Helen Street
    <label>City/Sate/ZIP:</label> Barberton, OH 44203
  </div>
</div>
</body></html>
"""

# Variant: portal typo handled — "City/Sate/ZIP:" (6 chars between City and ZIP).
SAMPLE_DETAIL_HTML_PORTAL_TYPO = """
<html><body>
<div class="card card--parties-MV">
  <div class="card-header"><h4>Plaintiff</h4></div>
  <div class="card-body">
    <label>Plaintiff 1:</label> Summit Rental Properties, Llc
    <label>Address:</label> PO Box 1
    <label>City/Sate/ZIP:</label> Barberton, Oh 44203
  </div>
</div>
<div class="card card--parties-MV">
  <div class="card-header"><h4>Defendants</h4></div>
  <div class="card-body">
    <label>Defendant 1:</label> Tenant Name
    <label>Address:</label> 583 W. Lake Avenue, #3
    <label>City/Sate/ZIP:</label> Barberton, Oh 44203
  </div>
</div>
</body></html>
"""

SAMPLE_DETAIL_HTML_NO_ADDRESS = """
<html><body>
<div class="card card--parties-MV">
  <div class="card-header"><h4>Plaintiff</h4></div>
  <div class="card-body">
    <label>Plaintiff 1:</label> Some Landlord LLC
  </div>
</div>
<div class="card card--parties-MV">
  <div class="card-header"><h4>Defendants</h4></div>
  <div class="card-body">
    <label>Defendant 1:</label> Doe, Jane
  </div>
</div>
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
# _parse_search_results
# ---------------------------------------------------------------------------

def test_parse_search_results_returns_only_cvg_cases():
    filings = _parse_search_results(SAMPLE_SEARCH_HTML, search_date=SEARCH_DATE)
    assert len(filings) == 2
    assert all(f.case_number.startswith("CVG") for f in filings)


def test_parse_search_results_maps_case_number():
    filings = _parse_search_results(SAMPLE_SEARCH_HTML, search_date=SEARCH_DATE)
    assert filings[0].case_number == "CVG2601199"
    assert filings[1].case_number == "CVG2601200"


def test_parse_search_results_strips_occupant_suffix_from_tenant():
    filings = _parse_search_results(SAMPLE_SEARCH_HTML, search_date=SEARCH_DATE)
    assert filings[0].tenant_name == "Brown, Rebecca"
    assert filings[1].tenant_name == "Walker, Robert"


def test_parse_search_results_landlord_is_unknown_placeholder():
    # Landlord is not available in search results; upgraded later via _fetch_case_detail.
    filings = _parse_search_results(SAMPLE_SEARCH_HTML, search_date=SEARCH_DATE)
    assert filings[0].landlord_name == "Unknown"


def test_parse_search_results_address_is_unknown_placeholder():
    filings = _parse_search_results(SAMPLE_SEARCH_HTML, search_date=SEARCH_DATE)
    assert filings[0].property_address == "Unknown"


def test_parse_search_results_sets_filing_date_to_search_date():
    filings = _parse_search_results(SAMPLE_SEARCH_HTML, search_date=SEARCH_DATE)
    assert filings[0].filing_date == SEARCH_DATE


def test_parse_search_results_sets_county_state_notice_type():
    filings = _parse_search_results(SAMPLE_SEARCH_HTML, search_date=SEARCH_DATE)
    assert filings[0].state == "OH"
    assert filings[0].county == "Summit"
    assert filings[0].notice_type == "Eviction"


def test_parse_search_results_source_url_is_absolute():
    filings = _parse_search_results(SAMPLE_SEARCH_HTML, search_date=SEARCH_DATE)
    assert filings[0].source_url.startswith("https://")
    assert "tok111" in filings[0].source_url


def test_parse_search_results_returns_empty_for_blank_page():
    filings = _parse_search_results(
        "<html><body><p>No results.</p></body></html>",
        search_date=SEARCH_DATE,
    )
    assert filings == []


def test_parse_search_results_placeholder_tenant_falls_back_to_unknown(monkeypatch):
    """When clean_tenant_name returns '' (junk name), fall back to 'Unknown'."""
    import scrapers.ohio.barberton as mod
    monkeypatch.setattr(mod, "clean_tenant_name", lambda _: "")

    filings = _parse_search_results(SAMPLE_SEARCH_HTML, search_date=SEARCH_DATE)
    assert len(filings) > 0
    assert all(f.tenant_name == "Unknown" for f in filings)


# ---------------------------------------------------------------------------
# _fetch_case_detail
# ---------------------------------------------------------------------------

class TestFetchCaseDetail:
    def _mock_session(self, html: str, status: int = 200):
        from unittest.mock import MagicMock
        mock_resp = MagicMock()
        mock_resp.status_code = status
        mock_resp.text = html
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        return mock_session

    def test_returns_address_and_landlord(self):
        session = self._mock_session(SAMPLE_DETAIL_HTML)
        address, landlord = _fetch_case_detail(session, "https://example.com/record/7721/tok111")
        assert address == "87 Helen Street, Barberton, OH 44203"
        assert landlord == "M&C MHP LLC"

    def test_handles_portal_typo_city_sate_zip(self):
        """City/Sate/ZIP: (typo) must match — regex is City.*ZIP."""
        session = self._mock_session(SAMPLE_DETAIL_HTML_PORTAL_TYPO)
        address, landlord = _fetch_case_detail(session, "https://example.com/record/7721/tok999")
        assert address == "583 W. Lake Avenue, #3, Barberton, Oh 44203"
        assert landlord == "Summit Rental Properties, Llc"

    def test_returns_none_when_address_labels_absent(self):
        session = self._mock_session(SAMPLE_DETAIL_HTML_NO_ADDRESS)
        address, landlord = _fetch_case_detail(session, "https://example.com/record/7721/tok000")
        assert address is None

    def test_returns_none_on_http_error(self):
        session = self._mock_session("", status=404)
        address, landlord = _fetch_case_detail(session, "https://example.com/record/7721/bad")
        assert address is None
        assert landlord is None

    def test_returns_none_on_exception(self):
        from unittest.mock import MagicMock
        mock_session = MagicMock()
        mock_session.get.side_effect = Exception("connection timeout")
        address, landlord = _fetch_case_detail(mock_session, "https://example.com/record/7721/err")
        assert address is None
        assert landlord is None


# ---------------------------------------------------------------------------
# BarbertonMunicipalScraper — error handling and deduplication
# ---------------------------------------------------------------------------

def test_scraper_records_last_error_when_session_fails(monkeypatch):
    scraper = BarbertonMunicipalScraper(lookback_days=2)
    monkeypatch.setattr(scraper, "_ensure_session", lambda: False)

    filings = scraper.scrape()

    assert filings == []
    assert scraper.last_error is not None


def test_scraper_records_last_error_when_search_fails(monkeypatch):
    scraper = BarbertonMunicipalScraper(lookback_days=0)
    monkeypatch.setattr(scraper, "_ensure_session", lambda: True)
    scraper._session_ready = True

    def fail_get_search(_date):
        raise ConnectionResetError("connection reset")

    monkeypatch.setattr(scraper, "_get_search", fail_get_search)

    filings = scraper.scrape()

    assert filings == []
    assert "connection reset" in scraper.last_error


def test_scraper_dedupes_same_case_across_dates(monkeypatch):
    scraper = BarbertonMunicipalScraper(lookback_days=2)
    monkeypatch.setattr(scraper, "_ensure_session", lambda: True)
    scraper._session_ready = True

    monkeypatch.setattr(scraper, "_get_search", lambda _d: SAMPLE_SEARCH_HTML)
    monkeypatch.setattr(
        "scrapers.ohio.barberton._fetch_case_detail",
        lambda _session, _url: (None, None),
    )

    filings = scraper.scrape()

    case_numbers = [f.case_number for f in filings]
    assert len(case_numbers) == len(set(case_numbers)), "Duplicate case numbers found"


def test_scraper_upgrades_address_and_landlord_when_detail_succeeds(monkeypatch):
    scraper = BarbertonMunicipalScraper(lookback_days=0)
    monkeypatch.setattr(scraper, "_ensure_session", lambda: True)
    scraper._session_ready = True

    monkeypatch.setattr(scraper, "_get_search", lambda _d: SAMPLE_SEARCH_HTML)
    monkeypatch.setattr(
        "scrapers.ohio.barberton._fetch_case_detail",
        lambda _session, _url: ("87 Helen Street, Barberton, OH 44203", "M&C MHP LLC"),
    )

    filings = scraper.scrape()

    assert len(filings) > 0
    assert filings[0].property_address == "87 Helen Street, Barberton, OH 44203"
    assert filings[0].landlord_name == "M&C MHP LLC"


def test_scraper_keeps_unknown_placeholders_when_detail_returns_none(monkeypatch):
    scraper = BarbertonMunicipalScraper(lookback_days=0)
    monkeypatch.setattr(scraper, "_ensure_session", lambda: True)
    scraper._session_ready = True

    monkeypatch.setattr(scraper, "_get_search", lambda _d: SAMPLE_SEARCH_HTML)
    monkeypatch.setattr(
        "scrapers.ohio.barberton._fetch_case_detail",
        lambda _session, _url: (None, None),
    )

    filings = scraper.scrape()

    assert len(filings) > 0
    assert filings[0].property_address == "Unknown"
    assert filings[0].landlord_name == "Unknown"
