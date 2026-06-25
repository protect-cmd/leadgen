from __future__ import annotations

from datetime import date

from scrapers.ohio.lorain import (
    ElyriaMunicipalScraper,
    _fetch_defendant_address,
    _group_by_case,
    _parse_case_rows,
    _parse_filing_date,
    _strip_occupant_suffix,
)


# ---------------------------------------------------------------------------
# HTML fixtures — modelled on real CourtView (equivant) HTML structure.
# Confirmed case data: 2022CVG01367, JONES JODIE, 240 4TH ST APT A304, Elyria OH 44035
# ---------------------------------------------------------------------------

# Results table: two cases, each with two party rows (Plaintiff + Defendant)
# cells: [blank, blank, case#+link, case_type, file_date, initiating_action, party_name, party_type]
SAMPLE_RESULTS_HTML = """
<html><body>
<table>
  <tr>
    <th></th><th></th><th>Case Number</th><th>Case Type</th>
    <th>File Date</th><th>Initiating Action</th><th>Party Name</th><th>Party Type</th>
  </tr>
  <tr>
    <td></td><td></td>
    <td><a href="searchresults.page?x=tok111">2026CVG00101</a></td>
    <td>Eviction (CVG)</td>
    <td>05/10/2026</td>
    <td>FORCIBLE ENTRY</td>
    <td>SUNRISE PROPERTIES LLC</td>
    <td>Plaintiff</td>
  </tr>
  <tr>
    <td></td><td></td>
    <td><a href="searchresults.page?x=tok111">2026CVG00101</a></td>
    <td>Eviction (CVG)</td>
    <td>05/10/2026</td>
    <td>FORCIBLE ENTRY</td>
    <td>JONES, JODIE</td>
    <td>Defendant</td>
  </tr>
  <tr>
    <td></td><td></td>
    <td><a href="searchresults.page?x=tok222">2026CVG00102</a></td>
    <td>Eviction (CVG)</td>
    <td>05/10/2026</td>
    <td>FORCIBLE ENTRY</td>
    <td>OAK HILL RENTALS INC</td>
    <td>Plaintiff</td>
  </tr>
  <tr>
    <td></td><td></td>
    <td><a href="searchresults.page?x=tok222">2026CVG00102</a></td>
    <td>Eviction (CVG)</td>
    <td>05/10/2026</td>
    <td>FORCIBLE ENTRY</td>
    <td>SMITH, JOHN et al</td>
    <td>Defendant</td>
  </tr>
</table>
</body></html>
"""

SAMPLE_RESULTS_HTML_EMPTY = """
<html><body>
<p>No results found for the given search criteria.</p>
</body></html>
"""

# Case detail page with plaintiff first, then defendant.
# CourtView splits address across 5 separate lines: street / city / , / state / zip.
# Confirmed structure from real portal probe.
SAMPLE_DETAIL_HTML = """
<html><body>
<div class="party-info">
Party Information

SEIFERT, CARYN
- Plaintiff
Disposition
Disp Date
Address
4730 SIERRA LANE
COCONUT CREEK
,
FL
33073
Alias
Party Attorney

JONES, JODIE
- Defendant
Disposition
Disp Date
Address
240 4TH ST APT A304
ELYRIA
,
OH
44035
Alias
Party Attorney
</div>
</body></html>
"""

SAMPLE_DETAIL_HTML_NO_ADDRESS = """
<html><body>
<div class="party-info">
SOME LANDLORD LLC
- Plaintiff
Disposition
Disp Date
Address

DOE, JANE
- Defendant
Disposition
Disp Date
Address

</div>
</body></html>
"""


# ---------------------------------------------------------------------------
# _strip_occupant_suffix
# ---------------------------------------------------------------------------

def test_strip_occupant_suffix_removes_et_al():
    assert _strip_occupant_suffix("SMITH, JOHN et al") == "SMITH, JOHN"


def test_strip_occupant_suffix_removes_et_al_with_period():
    assert _strip_occupant_suffix("DOE, JANE et al.") == "DOE, JANE"


def test_strip_occupant_suffix_removes_and_all_other_occupants():
    assert _strip_occupant_suffix("JONES, BOB AND ALL OTHER OCCUPANTS") == "JONES, BOB"


def test_strip_occupant_suffix_leaves_plain_names_unchanged():
    assert _strip_occupant_suffix("JONES, JODIE") == "JONES, JODIE"


# ---------------------------------------------------------------------------
# _parse_case_rows
# ---------------------------------------------------------------------------

def test_parse_case_rows_returns_all_party_rows():
    rows = _parse_case_rows(SAMPLE_RESULTS_HTML)
    # 2 cases × 2 parties each = 4 rows
    assert len(rows) == 4


def test_parse_case_rows_maps_case_number():
    rows = _parse_case_rows(SAMPLE_RESULTS_HTML)
    case_numbers = {r["case_number"] for r in rows}
    assert "2026CVG00101" in case_numbers
    assert "2026CVG00102" in case_numbers


def test_parse_case_rows_maps_party_type():
    rows = _parse_case_rows(SAMPLE_RESULTS_HTML)
    types = [r["party_type"] for r in rows]
    assert "Plaintiff" in types
    assert "Defendant" in types


def test_parse_case_rows_maps_file_date():
    rows = _parse_case_rows(SAMPLE_RESULTS_HTML)
    assert rows[0]["file_date_str"] == "05/10/2026"


def test_parse_case_rows_makes_detail_href_absolute():
    rows = _parse_case_rows(SAMPLE_RESULTS_HTML)
    for row in rows:
        assert row["detail_href"].startswith("http")


def test_parse_case_rows_returns_empty_for_blank_page():
    rows = _parse_case_rows(SAMPLE_RESULTS_HTML_EMPTY)
    assert rows == []


# ---------------------------------------------------------------------------
# _group_by_case
# ---------------------------------------------------------------------------

def test_group_by_case_collapses_to_one_entry_per_case():
    rows = _parse_case_rows(SAMPLE_RESULTS_HTML)
    cases = _group_by_case(rows)
    assert len(cases) == 2


def test_group_by_case_picks_plaintiff_as_landlord():
    rows = _parse_case_rows(SAMPLE_RESULTS_HTML)
    cases = _group_by_case(rows)
    assert cases["2026CVG00101"]["landlord"] == "SUNRISE PROPERTIES LLC"


def test_group_by_case_picks_defendant_as_tenant():
    rows = _parse_case_rows(SAMPLE_RESULTS_HTML)
    cases = _group_by_case(rows)
    assert cases["2026CVG00101"]["tenant_raw"] == "JONES, JODIE"


def test_group_by_case_keeps_raw_tenant_name():
    rows = _parse_case_rows(SAMPLE_RESULTS_HTML)
    cases = _group_by_case(rows)
    # tenant_raw keeps the raw name; _strip_occupant_suffix is called in scrape()
    assert cases["2026CVG00102"]["tenant_raw"] == "SMITH, JOHN et al"


# ---------------------------------------------------------------------------
# _parse_filing_date
# ---------------------------------------------------------------------------

def test_parse_filing_date_parses_mm_dd_yyyy():
    assert _parse_filing_date("05/10/2026") == date(2026, 5, 10)


def test_parse_filing_date_returns_none_on_invalid():
    assert _parse_filing_date("not-a-date") is None


def test_parse_filing_date_returns_none_on_empty():
    assert _parse_filing_date("") is None


# ---------------------------------------------------------------------------
# _fetch_defendant_address
# ---------------------------------------------------------------------------

class TestFetchDefendantAddress:
    def test_returns_defendant_address(self):
        from unittest.mock import MagicMock
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = SAMPLE_DETAIL_HTML
        mock_session.get.return_value = mock_resp

        result = _fetch_defendant_address(
            mock_session,
            "https://eservices.elyriamunicourt.org/eservices/searchresults.page?x=tok111",
        )
        assert result == "240 4TH ST APT A304, ELYRIA, OH 44035"

    def test_no_double_spaces_in_result(self):
        """Address must not contain multiple consecutive spaces."""
        from unittest.mock import MagicMock
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = SAMPLE_DETAIL_HTML
        mock_session.get.return_value = mock_resp

        result = _fetch_defendant_address(mock_session, "https://example.com/x=tok")
        assert result is not None
        assert "  " not in result

    def test_skips_plaintiff_address_and_returns_defendant(self):
        """Must not return the Plaintiff's address (4730 SIERRA LANE, FL)."""
        from unittest.mock import MagicMock
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = SAMPLE_DETAIL_HTML
        mock_session.get.return_value = mock_resp

        result = _fetch_defendant_address(mock_session, "https://example.com/x=tok")
        assert result is not None
        assert "FL" not in result
        assert "OH" in result

    def test_returns_none_when_no_defendant_section(self):
        from unittest.mock import MagicMock
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = SAMPLE_DETAIL_HTML_NO_ADDRESS
        mock_session.get.return_value = mock_resp

        result = _fetch_defendant_address(mock_session, "https://example.com/x=nope")
        assert result is None

    def test_returns_none_on_http_error(self):
        from unittest.mock import MagicMock
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_session.get.return_value = mock_resp

        result = _fetch_defendant_address(mock_session, "https://example.com/x=bad")
        assert result is None

    def test_returns_none_on_exception(self):
        from unittest.mock import MagicMock
        mock_session = MagicMock()
        mock_session.get.side_effect = Exception("connection timeout")

        result = _fetch_defendant_address(mock_session, "https://example.com/x=tok")
        assert result is None


# ---------------------------------------------------------------------------
# ElyriaMunicipalScraper — error handling and deduplication
# ---------------------------------------------------------------------------

def test_scraper_records_last_error_when_search_fails(monkeypatch):
    scraper = ElyriaMunicipalScraper(lookback_days=2)

    def fail_search(_begin, _end):
        raise ConnectionResetError("connection reset")

    monkeypatch.setattr(scraper, "_search", fail_search)

    filings = scraper.scrape()

    assert filings == []
    assert scraper.last_error is not None
    assert "connection reset" in scraper.last_error


def test_scraper_no_error_when_search_succeeds_with_filings(monkeypatch):
    scraper = ElyriaMunicipalScraper(lookback_days=0)

    monkeypatch.setattr(scraper, "_search", lambda _b, _e: _parse_case_rows(SAMPLE_RESULTS_HTML))
    monkeypatch.setattr(
        "scrapers.ohio.lorain._fetch_defendant_address",
        lambda _session, _url: "240 4TH ST APT A304, ELYRIA, OH 44035",
    )

    filings = scraper.scrape()

    assert len(filings) > 0
    assert scraper.last_error is None


def test_scraper_dedupes_cases_from_results(monkeypatch):
    """Results page already groups by case — no duplicate case numbers."""
    scraper = ElyriaMunicipalScraper(lookback_days=0)

    monkeypatch.setattr(scraper, "_search", lambda _b, _e: _parse_case_rows(SAMPLE_RESULTS_HTML))
    monkeypatch.setattr(
        "scrapers.ohio.lorain._fetch_defendant_address",
        lambda _session, _url: None,
    )

    filings = scraper.scrape()

    case_numbers = [f.case_number for f in filings]
    assert len(case_numbers) == len(set(case_numbers))


def test_scraper_upgrades_placeholder_when_detail_succeeds(monkeypatch):
    scraper = ElyriaMunicipalScraper(lookback_days=0)

    monkeypatch.setattr(scraper, "_search", lambda _b, _e: _parse_case_rows(SAMPLE_RESULTS_HTML))
    monkeypatch.setattr(
        "scrapers.ohio.lorain._fetch_defendant_address",
        lambda _session, _url: "240 4TH ST APT A304, ELYRIA, OH 44035",
    )

    filings = scraper.scrape()

    assert len(filings) > 0
    assert filings[0].property_address == "240 4TH ST APT A304, ELYRIA, OH 44035"


def test_scraper_keeps_unknown_when_detail_returns_none(monkeypatch):
    scraper = ElyriaMunicipalScraper(lookback_days=0)

    monkeypatch.setattr(scraper, "_search", lambda _b, _e: _parse_case_rows(SAMPLE_RESULTS_HTML))
    monkeypatch.setattr(
        "scrapers.ohio.lorain._fetch_defendant_address",
        lambda _session, _url: None,
    )

    filings = scraper.scrape()

    assert len(filings) > 0
    assert all(f.property_address == "Unknown" for f in filings)


def test_scraper_strips_occupant_suffix_from_tenant(monkeypatch):
    scraper = ElyriaMunicipalScraper(lookback_days=0)

    monkeypatch.setattr(scraper, "_search", lambda _b, _e: _parse_case_rows(SAMPLE_RESULTS_HTML))
    monkeypatch.setattr(
        "scrapers.ohio.lorain._fetch_defendant_address",
        lambda _session, _url: None,
    )

    filings = scraper.scrape()

    # CVG00102 has "SMITH, JOHN et al" — suffix should be stripped
    case_102 = next(f for f in filings if f.case_number == "2026CVG00102")
    assert "et al" not in case_102.tenant_name


def test_scraper_sets_correct_county_state_notice_type(monkeypatch):
    scraper = ElyriaMunicipalScraper(lookback_days=0)

    monkeypatch.setattr(scraper, "_search", lambda _b, _e: _parse_case_rows(SAMPLE_RESULTS_HTML))
    monkeypatch.setattr(
        "scrapers.ohio.lorain._fetch_defendant_address",
        lambda _session, _url: None,
    )

    filings = scraper.scrape()

    assert len(filings) > 0
    assert filings[0].state == "OH"
    assert filings[0].county == "Lorain"
    assert filings[0].notice_type == "Eviction"


def test_scraper_placeholder_tenant_falls_back_to_unknown(monkeypatch):
    """When clean_tenant_name returns '' (garbage name), fall back to 'Unknown'."""
    import scrapers.ohio.lorain as mod
    monkeypatch.setattr(mod, "clean_tenant_name", lambda _: "")

    scraper = ElyriaMunicipalScraper(lookback_days=0)
    monkeypatch.setattr(scraper, "_search", lambda _b, _e: _parse_case_rows(SAMPLE_RESULTS_HTML))
    monkeypatch.setattr(
        "scrapers.ohio.lorain._fetch_defendant_address",
        lambda _session, _url: None,
    )

    filings = scraper.scrape()

    assert len(filings) > 0
    assert all(f.tenant_name == "Unknown" for f in filings)


# ---------------------------------------------------------------------------
# _search — Playwright-based flow
# ---------------------------------------------------------------------------

def test_search_raises_when_playwright_fails(monkeypatch):
    """_search must propagate RuntimeError when Playwright cannot render the portal."""
    import pytest
    import scrapers.ohio.lorain as mod

    def _pw_fail(begin, end):
        raise RuntimeError("Lorain: React home did not render Case Search card")

    monkeypatch.setattr(mod, "_playwright_search", _pw_fail)

    scraper = ElyriaMunicipalScraper(lookback_days=2)
    with pytest.raises(RuntimeError, match="React home did not render"):
        scraper._search(date(2026, 1, 1), date(2026, 1, 7))
