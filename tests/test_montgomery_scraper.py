from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from scrapers.ohio.montgomery import (
    MontgomeryCountyMunicipalScraper,
    _normalize_address,
    _parse_address,
    _parse_court_date,
    _parse_results_page,
    _strip_occupant_suffix,
)
from pipeline import gates

# ---------------------------------------------------------------------------
# Sample HTML fixtures
# ---------------------------------------------------------------------------

RESULTS_HTML = """
<html><body>
<table>
<thead>
  <tr><th>Case Number</th><th>Plaintiff</th><th>Defendant</th><th>Status</th></tr>
</thead>
<tbody>
  <tr>
    <td><a href="CvCaseSummary.cfm?cid=ABC123">2026-CVG-003517</a></td>
    <td>GD EASTERN, LLC</td>
    <td>Scott Pierce and All Others</td>
    <td>Pending</td>
  </tr>
  <tr>
    <td><a href="CvCaseSummary.cfm?cid=DEF456">2026-CVG-003522</a></td>
    <td>Crawford Communities, LLC</td>
    <td>Tammy Von Berge and All Others</td>
    <td>Pending</td>
  </tr>
  <tr>
    <td><a href="CvCaseSummary.cfm?cid=GHI789">2026-CVF-001234</a></td>
    <td>Citibank, N.A.</td>
    <td>Eva Valtierra</td>
    <td>Pending</td>
  </tr>
  <tr>
    <td><a href="CvCaseSummary.cfm?cid=JKL012">2026-CVH-003513</a></td>
    <td>Montgomery County Animal Resource Center</td>
    <td>Zowie Fultz</td>
    <td>Pending</td>
  </tr>
  <tr>
    <td><a href="CvCaseSummary.cfm?cid=MNO345">2026-CVG-003533</a></td>
    <td></td>
    <td></td>
    <td>Pending</td>
  </tr>
</tbody>
</table>
</body></html>
"""

DETAIL_TEXT_FULL = """\
Civil Case Summary: 2026-CVG-003517
Forcible Entry & DetainerLocaton: 301 Troy St., Apt. 7, Dayton, Oh, 45404
Case Number:
2026-CVG-003517
Plaintiff:
GD EASTERN, LLC
Defendant:
SCOTT PIERCE AND ALL OTHERS
Next Court Date:
06/18/2026
Case History
Date Time Event
05/27/2026 12:15PM New Forcible Entry & Detainer Case created
05/28/2026 9:26AM Eviction Location: 301 Troy St., Apt. 7, Dayton, Oh, 45404
"""

DETAIL_TEXT_NO_LOCATON = """\
Civil Case Summary: 2026-CVG-003522
Forcible Entry & Detainer
Case Number:
2026-CVG-003522
Plaintiff:
Crawford Communities, LLC
Defendant:
TAMMY VON BERGE AND ALL OTHERS
Next Court Date:
06/25/2026
Case History
Date Time Event
05/27/2026 11:00AM New Forcible Entry & Detainer Case created
05/28/2026 10:00AM Eviction Location: 5100 Springfield St., Dayton, Oh, 45431
"""

DETAIL_TEXT_NO_COURT_DATE = """\
Civil Case Summary: 2026-CVG-003533
Forcible Entry & DetainerLocaton: 44 W 4th St., Dayton, Oh, 45402
Case Number:
2026-CVG-003533
Next Court Date:
"""

DETAIL_TEXT_LOCATION_TYPO_FIXED = """\
Civil Case Summary: 2026-CVG-009999
Forcible Entry & DetainerLocation: 999 Main St., Dayton, Oh, 45404
Next Court Date:
07/01/2026
"""

# ---------------------------------------------------------------------------
# _parse_results_page
# ---------------------------------------------------------------------------


def test_parse_results_page_returns_only_cvg_cases():
    rows = _parse_results_page(RESULTS_HTML)
    case_numbers = [r["case_number"] for r in rows]
    assert "2026-CVG-003517" in case_numbers
    assert "2026-CVG-003522" in case_numbers
    assert "2026-CVG-003533" in case_numbers


def test_parse_results_page_excludes_non_cvg():
    rows = _parse_results_page(RESULTS_HTML)
    case_numbers = [r["case_number"] for r in rows]
    assert "2026-CVF-001234" not in case_numbers
    assert "2026-CVH-003513" not in case_numbers


def test_parse_results_page_count():
    rows = _parse_results_page(RESULTS_HTML)
    assert len(rows) == 3


def test_parse_results_page_plaintiff_and_defendant():
    rows = _parse_results_page(RESULTS_HTML)
    first = rows[0]
    assert first["plaintiff"] == "GD EASTERN, LLC"
    assert first["defendant_raw"] == "Scott Pierce and All Others"


def test_parse_results_page_case_url_contains_base():
    rows = _parse_results_page(RESULTS_HTML)
    assert rows[0]["case_url"].startswith("https://clerkofcourt.daytonohio.gov/PA/")
    assert "CvCaseSummary.cfm?cid=ABC123" in rows[0]["case_url"]


def test_parse_results_page_empty_plaintiff_and_defendant_for_sparse_row():
    rows = _parse_results_page(RESULTS_HTML)
    sparse = next(r for r in rows if r["case_number"] == "2026-CVG-003533")
    assert sparse["plaintiff"] == ""
    assert sparse["defendant_raw"] == ""


def test_parse_results_page_empty_html_returns_empty_list():
    assert _parse_results_page("<html><body></body></html>") == []


# ---------------------------------------------------------------------------
# _parse_address
# ---------------------------------------------------------------------------


def test_parse_address_extracts_locaton_typo():
    addr = _parse_address(DETAIL_TEXT_FULL)
    assert addr == "301 Troy St., Apt. 7, Dayton, Oh, 45404"


def test_parse_address_falls_back_to_eviction_location_in_history():
    addr = _parse_address(DETAIL_TEXT_NO_LOCATON)
    assert addr == "5100 Springfield St., Dayton, Oh, 45431"


def test_parse_address_handles_corrected_location_spelling():
    addr = _parse_address(DETAIL_TEXT_LOCATION_TYPO_FIXED)
    assert addr == "999 Main St., Dayton, Oh, 45404"


def test_parse_address_returns_none_when_no_address():
    addr = _parse_address("Some page with no location info.\nNext Court Date:\n07/01/2026")
    assert addr is None


# ---------------------------------------------------------------------------
# _normalize_address  (gate-passing form)
# ---------------------------------------------------------------------------


def test_normalize_address_uppercases_state_and_drops_comma():
    assert (
        _normalize_address("534 Oxford Avenue, Dayton, Oh, 45402")
        == "534 Oxford Avenue, Dayton, OH 45402"
    )


def test_normalize_address_with_apt_and_periods():
    assert (
        _normalize_address("301 Troy St., Apt. 7, Dayton, Oh, 45404")
        == "301 Troy St., Apt. 7, Dayton, OH 45404"
    )


def test_normalize_address_passes_gate_after_fix():
    raw = "5100 Springfield St., Dayton, Oh, 45431"
    assert not gates.gate_address(raw)  # fails before normalization
    assert gates.gate_address(_normalize_address(raw))  # passes after


def test_normalize_address_leaves_correct_form_untouched():
    good = "100 Main St, Dayton, OH 45402"
    assert _normalize_address(good) == good


def test_normalize_address_passthrough_unknown_and_none():
    assert _normalize_address("Unknown") == "Unknown"
    assert _normalize_address(None) is None


def test_normalize_address_handles_nine_digit_zip():
    assert (
        _normalize_address("44 W 4th St., Dayton, Oh, 45402-1234")
        == "44 W 4th St., Dayton, OH 45402"
    )


# ---------------------------------------------------------------------------
# _parse_court_date
# ---------------------------------------------------------------------------


def test_parse_court_date_extracts_date():
    d = _parse_court_date(DETAIL_TEXT_FULL)
    assert d == date(2026, 6, 18)


def test_parse_court_date_handles_date_on_next_line():
    text = "Next Court Date:\n06/25/2026\nMore text"
    d = _parse_court_date(text)
    assert d == date(2026, 6, 25)


def test_parse_court_date_returns_none_when_missing():
    d = _parse_court_date(DETAIL_TEXT_NO_COURT_DATE)
    assert d is None


def test_parse_court_date_returns_none_for_empty_text():
    assert _parse_court_date("") is None


# ---------------------------------------------------------------------------
# _strip_occupant_suffix
# ---------------------------------------------------------------------------


def test_strip_occupant_suffix_and_all_others():
    assert _strip_occupant_suffix("Scott Pierce and All Others") == "Scott Pierce"


def test_strip_occupant_suffix_and_all_other_occupants():
    assert _strip_occupant_suffix("Jane Smith and All Other Occupants") == "Jane Smith"


def test_strip_occupant_suffix_et_al():
    assert _strip_occupant_suffix("John Doe et al.") == "John Doe"


def test_strip_occupant_suffix_leaves_plain_name():
    assert _strip_occupant_suffix("Curtis Nagy") == "Curtis Nagy"


def test_strip_occupant_suffix_case_insensitive():
    assert _strip_occupant_suffix("JAMES DENSON AND ALL OTHERS") == "JAMES DENSON"


# ---------------------------------------------------------------------------
# MontgomeryCountyMunicipalScraper.scrape()
# ---------------------------------------------------------------------------


def test_scraper_records_last_error_on_network_failure(monkeypatch):
    scraper = MontgomeryCountyMunicipalScraper(lookback_days=0)

    def fail(_url: str) -> str:
        raise ConnectionError("portal down")

    monkeypatch.setattr(scraper, "_get_text", fail)

    filings = scraper.scrape()

    assert filings == []
    assert "portal down" in scraper.last_error


def test_scraper_returns_empty_list_when_no_cvg_cases(monkeypatch):
    scraper = MontgomeryCountyMunicipalScraper(lookback_days=0)

    def fake_get(url: str) -> str:
        return "<html><body><table></table></body></html>"

    monkeypatch.setattr(scraper, "_get_text", fake_get)

    filings = scraper.scrape()

    assert filings == []
    assert scraper.last_error is None


def test_scraper_dedupes_same_case_across_lookback_days(monkeypatch):
    scraper = MontgomeryCountyMunicipalScraper(lookback_days=2)

    def fake_get(url: str) -> str:
        if "CvCaseSummary" in url:
            return f"<html><body>{DETAIL_TEXT_FULL}</body></html>"
        # Every day returns the same case
        return RESULTS_HTML

    monkeypatch.setattr(scraper, "_get_text", fake_get)

    filings = scraper.scrape()

    case_numbers = [f.case_number for f in filings]
    assert len(case_numbers) == len(set(case_numbers))


def test_scraper_sets_correct_state_county_notice_type(monkeypatch):
    scraper = MontgomeryCountyMunicipalScraper(lookback_days=0)

    call_count = 0

    def fake_get(url: str) -> str:
        nonlocal call_count
        call_count += 1
        if "CvCaseSummary" in url:
            return f"<html><body>{DETAIL_TEXT_FULL}</body></html>"
        return RESULTS_HTML

    monkeypatch.setattr(scraper, "_get_text", fake_get)

    filings = scraper.scrape()

    assert len(filings) > 0
    for f in filings:
        assert f.state == "OH"
        assert f.county == "Montgomery"
        assert f.notice_type == "Forcible Entry & Detainer"


def test_scraper_uses_unknown_fallback_when_no_address(monkeypatch):
    scraper = MontgomeryCountyMunicipalScraper(lookback_days=0)

    sparse_results = """
    <html><body><table><tbody>
    <tr>
      <td><a href="CvCaseSummary.cfm?cid=XYZ">2026-CVG-099999</a></td>
      <td>Some LLC</td><td>No Address Tenant</td><td>Pending</td>
    </tr>
    </tbody></table></body></html>
    """

    def fake_get(url: str) -> str:
        if "CvCaseSummary" in url:
            return "<html><body>No location info here.</body></html>"
        return sparse_results

    monkeypatch.setattr(scraper, "_get_text", fake_get)

    filings = scraper.scrape()

    assert len(filings) == 1
    assert filings[0].property_address == "Unknown"


def test_scraper_continues_when_detail_fetch_fails(monkeypatch):
    scraper = MontgomeryCountyMunicipalScraper(lookback_days=0)

    def fake_get(url: str) -> str:
        if "CvCaseSummary" in url:
            raise ConnectionError("detail page down")
        return RESULTS_HTML

    monkeypatch.setattr(scraper, "_get_text", fake_get)

    filings = scraper.scrape()

    # Should still produce filings with "Unknown" fallback address
    assert len(filings) > 0
    assert all(f.property_address == "Unknown" for f in filings)
    # Main scraper last_error should still be None (detail errors are warnings, not fatal)
    assert scraper.last_error is None


def test_scraper_strips_occupant_suffix_from_tenant_name(monkeypatch):
    scraper = MontgomeryCountyMunicipalScraper(lookback_days=0)

    single_case = """
    <html><body><table><tbody>
    <tr>
      <td><a href="CvCaseSummary.cfm?cid=T01">2026-CVG-000001</a></td>
      <td>Landlord LLC</td><td>Marcus Webb and All Others</td><td>Pending</td>
    </tr>
    </tbody></table></body></html>
    """

    def fake_get(url: str) -> str:
        if "CvCaseSummary" in url:
            return f"<html><body>{DETAIL_TEXT_FULL}</body></html>"
        return single_case

    monkeypatch.setattr(scraper, "_get_text", fake_get)

    filings = scraper.scrape()

    assert len(filings) == 1
    assert "and All Others" not in filings[0].tenant_name
    assert filings[0].tenant_name == "Marcus Webb"


def test_scraper_placeholder_defendant_becomes_unknown(monkeypatch):
    """A placeholder defendant (e.g. 'Jane Doe and All Others') must NOT leak
    through as a raw junk name — clean_tenant_name rejects it, so the scraper
    falls back to 'Unknown' (which pipeline gate_name drops)."""
    scraper = MontgomeryCountyMunicipalScraper(lookback_days=0)

    single_case = """
    <html><body><table><tbody>
    <tr>
      <td><a href="CvCaseSummary.cfm?cid=T02">2026-CVG-000002</a></td>
      <td>Landlord LLC</td><td>Jane Doe and All Others</td><td>Pending</td>
    </tr>
    </tbody></table></body></html>
    """

    def fake_get(url: str) -> str:
        if "CvCaseSummary" in url:
            return f"<html><body>{DETAIL_TEXT_FULL}</body></html>"
        return single_case

    monkeypatch.setattr(scraper, "_get_text", fake_get)

    filings = scraper.scrape()

    assert len(filings) == 1
    assert filings[0].tenant_name == "Unknown"


def test_scraper_lookback_floored_to_address_lag(monkeypatch):
    """Even with a small cron lookback, Montgomery re-scrapes at least
    _ADDRESS_LAG_DAYS days so late-populated addresses get re-visited and
    backfilled."""
    import re as _re

    from scrapers.ohio.montgomery import _ADDRESS_LAG_DAYS

    scraper = MontgomeryCountyMunicipalScraper(lookback_days=0)
    searched_dates: list[str] = []

    def fake_get(url: str) -> str:
        if "CvSearchResults" in url:
            m = _re.search(r"runDate=(\d{4}-\d{2}-\d{2})", url)
            if m:
                searched_dates.append(m.group(1))
            return "<html><body><table></table></body></html>"
        return "<html></html>"

    monkeypatch.setattr(scraper, "_get_text", fake_get)

    scraper.scrape()

    # offsets 0.._ADDRESS_LAG_DAYS inclusive, all distinct
    assert len(searched_dates) == _ADDRESS_LAG_DAYS + 1
    assert len(set(searched_dates)) == _ADDRESS_LAG_DAYS + 1
