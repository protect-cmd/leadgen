"""
Tests for Shelby County (Memphis) TN scraper.

Covers the PR template's quality-gate checklist:
  - Output schema matches Filing contract
  - Pagination works AND terminates safely on server loops
  - No crash on empty results, timeouts, or malformed pages
  - Address hit rate measurable from output
  - Smoke test (live network, opt-in via SHELBY_SMOKE=1) returns >= 50 filings
"""
from __future__ import annotations

import os
import re
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
import requests

from models.filing import Filing
from scrapers.tennessee.shelby import (
    ShelbyTNScraper,
    _clean_name,
    _extract_case_line,
    _join_address,
    _parse_results_page,
    _split_caption,
)


# --- Fixtures: realistic Contexte HTML samples ----------------------------------

# Two DEFENDANT rows + one ATTORNEY row (must be filtered out).
_SAMPLE_RESULTS_HTML = """
<html><body>
<font face="Arial" size="2">
<table border="1">
<tr>
  <td><b>ID</b></td>
  <td><b>Name/Corporation</b></td>
  <td><b>Address</b></td>
  <td><b>Party Type</b></td>
  <td><b>Filing Date</b></td>
</tr>
<tr>
  <td>@1957081</td>
  <td>SMITH, BE'DAUN<br>Case:  2382925  BACK OFFICE CENTRAL, LLC V BE'DAUN SMITH OR OCCU</td>
  <td>2044 YOUNG AVENUE<br>Memphis TN 38104</td>
  <td>DEFENDANT</td>
  <td>14-MAY-2026</td>
</tr>
<tr>
  <td>@1957527</td>
  <td>SMITH, MAKAYLA<br>Case:  2383302  THE PARK AT PAISLEY V MAKAYLA SMITH</td>
  <td>6090 BRAXTON CT #203<br>Memphis TN 38115</td>
  <td>DEFENDANT</td>
  <td>12-MAY-2026</td>
</tr>
<tr>
  <td>S008513</td>
  <td>SMITH, BRUCE M<br>Case:  2380564  NATHAN B BARLEY ETAL V EURONDA ROBERTSON ETAL</td>
  <td>396 WELLINGTON COVE<br>Memphis TN 38117</td>
  <td>ATTORNEY FOR PLAINTIFF</td>
  <td>01-MAY-2026</td>
</tr>
</table>
</font>
</body></html>
"""

_EMPTY_RESULTS_HTML = """
<html><body>
<font face="Arial" size="2">
<table>
<tr><td><b>ID</b></td><td><b>Name/Corporation</b></td><td><b>Address</b></td><td><b>Party Type</b></td><td><b>Filing Date</b></td></tr>
</table>
<p>No records found.</p>
</font>
</body></html>
"""

_MALFORMED_HTML = "<html><body><p>Server error 500</p></body></html>"


# --- Parser unit tests ----------------------------------------------------------

def test_parse_results_returns_three_rows():
    rows = _parse_results_page(_SAMPLE_RESULTS_HTML)
    assert len(rows) == 3
    assert rows[0]["case_number"] == "2382925"
    assert rows[0]["defendant"] == "SMITH, BE'DAUN"
    assert rows[0]["plaintiff"] == "BACK OFFICE CENTRAL, LLC"
    assert rows[0]["party_type"] == "DEFENDANT"
    assert rows[0]["filing_date"] == date(2026, 5, 14)


def test_parse_results_extracts_full_address():
    rows = _parse_results_page(_SAMPLE_RESULTS_HTML)
    assert "2044 YOUNG AVENUE" in rows[0]["address"]
    assert "Memphis TN 38104" in rows[0]["address"]
    # Apartment number preserved.
    assert "#203" in rows[1]["address"]


def test_parse_results_preserves_party_type_for_filtering():
    rows = _parse_results_page(_SAMPLE_RESULTS_HTML)
    party_types = {r["party_type"] for r in rows}
    assert "DEFENDANT" in party_types
    assert "ATTORNEY FOR PLAINTIFF" in party_types
    # The scraper itself filters; the parser keeps everything.


def test_parse_results_empty_page_returns_empty_list():
    assert _parse_results_page(_EMPTY_RESULTS_HTML) == []


def test_parse_results_malformed_page_does_not_crash():
    assert _parse_results_page(_MALFORMED_HTML) == []


def test_clean_name_strips_occupants_suffix():
    assert _clean_name("BE'DAUN SMITH OR OCCU") == "BE'DAUN SMITH"
    assert _clean_name("BOBBIE SMITH/OCCUPANTS") == "BOBBIE SMITH"
    assert _clean_name("CHLOE SMITH & ETAL") == "CHLOE SMITH"
    assert _clean_name("MAKAYLA SMITH") == "MAKAYLA SMITH"


def test_split_caption_handles_v_and_vs_separators():
    assert _split_caption("BACK OFFICE CENTRAL V SMITH") == ("BACK OFFICE CENTRAL", "SMITH")
    assert _split_caption("REEDY CO VS DASHANEA SMITH") == ("REEDY CO", "DASHANEA SMITH")
    assert _split_caption("") == ("", "")


def test_extract_case_line_finds_case_number():
    lines = ["SMITH, MAKAYLA", "Case:  2383302  THE PARK AT PAISLEY V MAKAYLA SMITH"]
    case_num, caption = _extract_case_line(lines)
    assert case_num == "2383302"
    assert caption == "THE PARK AT PAISLEY V MAKAYLA SMITH"


def test_extract_case_line_returns_empty_when_no_case():
    assert _extract_case_line(["SMITH, MAKAYLA"]) == ("", "")


def test_join_address_drops_case_continuation():
    assert _join_address(["6090 BRAXTON CT #203", "Memphis TN 38115"]) == "6090 BRAXTON CT #203, Memphis TN 38115"
    assert _join_address([]) == ""


# --- Scraper integration tests (mocked HTTP) ------------------------------------

def _mock_response(text: str, status: int = 200) -> MagicMock:
    m = MagicMock()
    m.text = text
    m.status_code = status
    m.raise_for_status = MagicMock()
    return m


def test_scraper_returns_filings_matching_contract():
    """Output must match the Filing scraper contract used by Davidson."""
    scraper = ShelbyTNScraper(lookback_days=7)

    with patch.object(scraper.session, "post") as mock_post, \
         patch.object(scraper.session, "get") as mock_get:
        mock_get.return_value = _mock_response("<html></html>")
        mock_post.return_value = _mock_response(_SAMPLE_RESULTS_HTML)

        filings = scraper.scrape()

    # Filings produced; attorney row dropped; both DEFENDANT case numbers present.
    # Every letter sweep returns the same 2 DEFENDANT rows in this mock,
    # but dedup on case_number means we end with 2 unique filings.
    case_numbers = {f.case_number for f in filings}
    assert "2382925" in case_numbers
    assert "2383302" in case_numbers
    assert len(filings) == 2  # dedupe held

    # Schema check — every field on the contract is present and typed correctly.
    f = next(f for f in filings if f.case_number == "2382925")
    assert isinstance(f, Filing)
    assert f.state == "TN"
    assert f.county == "Shelby"
    assert f.tenant_name == "SMITH, BE'DAUN"
    assert "2044 YOUNG AVENUE" in f.property_address
    assert f.landlord_name.startswith("BACK OFFICE CENTRAL")
    assert f.filing_date == date(2026, 5, 14)
    assert f.notice_type == "Detainer Warrant"


def test_scraper_filters_non_defendant_rows():
    scraper = ShelbyTNScraper(lookback_days=7)

    with patch.object(scraper.session, "post") as mock_post, \
         patch.object(scraper.session, "get") as mock_get:
        mock_get.return_value = _mock_response("<html></html>")
        mock_post.return_value = _mock_response(_SAMPLE_RESULTS_HTML)
        filings = scraper.scrape()

    # Bruce Smith was ATTORNEY FOR PLAINTIFF — must not appear.
    for f in filings:
        assert "BRUCE" not in f.tenant_name.upper()


def test_scraper_handles_empty_results_gracefully():
    scraper = ShelbyTNScraper(lookback_days=7)
    with patch.object(scraper.session, "post") as mock_post, \
         patch.object(scraper.session, "get") as mock_get:
        mock_get.return_value = _mock_response("<html></html>")
        mock_post.return_value = _mock_response(_EMPTY_RESULTS_HTML)
        filings = scraper.scrape()
    assert filings == []
    assert scraper.last_error is None


def test_scraper_handles_request_timeout_without_crashing():
    scraper = ShelbyTNScraper(lookback_days=7)
    with patch.object(scraper.session, "post", side_effect=requests.Timeout("timeout")), \
         patch.object(scraper.session, "get") as mock_get:
        mock_get.return_value = _mock_response("<html></html>")
        filings = scraper.scrape()
    # Every per-letter search fails individually — scraper logs and continues.
    assert filings == []


def test_scraper_handles_malformed_html():
    scraper = ShelbyTNScraper(lookback_days=7)
    with patch.object(scraper.session, "post") as mock_post, \
         patch.object(scraper.session, "get") as mock_get:
        mock_get.return_value = _mock_response("<html></html>")
        mock_post.return_value = _mock_response(_MALFORMED_HTML)
        filings = scraper.scrape()
    assert filings == []


def test_scraper_pagination_advances_when_page_full():
    """If a results page returns rows, the scraper must request the next page."""
    # Build a 30-row results table for page 1; page 2 returns empty.
    big_html_rows = "\n".join(
        f'<tr><td>R{i}</td><td>SMITH, X{i}<br>Case:  100{i:04d}  ACME V X{i}</td>'
        f'<td>{100+i} MAIN ST<br>Memphis TN 38103</td><td>DEFENDANT</td><td>01-MAY-2026</td></tr>'
        for i in range(30)
    )
    big_html = f"""
    <html><body><table>
    <tr><td><b>ID</b></td><td><b>Name/Corporation</b></td><td><b>Address</b></td><td><b>Party Type</b></td><td><b>Filing Date</b></td></tr>
    {big_html_rows}
    </table></body></html>
    """

    scraper = ShelbyTNScraper(lookback_days=7)
    call_log = []

    def fake_post(url, data, timeout):
        call_log.append(int(data["PageNo"]))
        # Page 1 returns a full 30; page 2 returns empty to terminate.
        if data["PageNo"] == "1":
            return _mock_response(big_html)
        return _mock_response(_EMPTY_RESULTS_HTML)

    with patch.object(scraper.session, "post", side_effect=fake_post), \
         patch.object(scraper.session, "get") as mock_get:
        mock_get.return_value = _mock_response("<html></html>")
        # Only do one letter for clarity.
        with patch("scrapers.tennessee.shelby._ALPHABET", ["A"]):
            scraper.scrape()

    # The scraper must have asked for page 2 after page 1 had results.
    assert 1 in call_log
    assert 2 in call_log


def test_scraper_pagination_terminates_on_same_page_set_loop():
    """Some ASP/Oracle servers loop the last page rather than returning empty.
    The scraper must detect this and stop, not loop forever."""
    rows_html = (
        '<tr><td>R1</td><td>SMITH, ALICE<br>Case:  111111  ACME V ALICE</td>'
        '<td>1 MAIN ST<br>Memphis TN 38103</td><td>DEFENDANT</td><td>01-MAY-2026</td></tr>'
    )
    looped_html = f"""
    <html><body><table>
    <tr><td><b>ID</b></td><td><b>Name/Corporation</b></td><td><b>Address</b></td><td><b>Party Type</b></td><td><b>Filing Date</b></td></tr>
    {rows_html}
    </table></body></html>
    """

    scraper = ShelbyTNScraper(lookback_days=7)
    call_log = []

    def fake_post(url, data, timeout):
        call_log.append(int(data["PageNo"]))
        return _mock_response(looped_html)  # always the same row

    with patch.object(scraper.session, "post", side_effect=fake_post), \
         patch.object(scraper.session, "get") as mock_get:
        mock_get.return_value = _mock_response("<html></html>")
        with patch("scrapers.tennessee.shelby._ALPHABET", ["A"]):
            scraper.scrape()

    # Must have stopped after detecting the repeat — exactly 2 calls (page 1
    # captured, page 2 detected as duplicate set). Definitely not 50 (hard cap).
    assert len(call_log) == 2, f"Expected 2 page fetches before loop detection, got {len(call_log)}"


def test_scraper_dedupes_repeated_case_numbers_across_letters():
    """A defendant whose name starts with two indexed letters won't be doubled."""
    scraper = ShelbyTNScraper(lookback_days=7)
    with patch.object(scraper.session, "post") as mock_post, \
         patch.object(scraper.session, "get") as mock_get:
        mock_get.return_value = _mock_response("<html></html>")
        # Every letter sweep returns the same row.
        mock_post.return_value = _mock_response(_SAMPLE_RESULTS_HTML)
        filings = scraper.scrape()
    # Unique case numbers despite 26 letters returning the same rows.
    assert len({f.case_number for f in filings}) == len(filings)


def test_scraper_measures_address_hit_rate():
    """Quality gate requires >=60% address hit rate. Compute from output."""
    scraper = ShelbyTNScraper(lookback_days=7)
    with patch.object(scraper.session, "post") as mock_post, \
         patch.object(scraper.session, "get") as mock_get:
        mock_get.return_value = _mock_response("<html></html>")
        mock_post.return_value = _mock_response(_SAMPLE_RESULTS_HTML)
        filings = scraper.scrape()

    if not filings:
        pytest.skip("No filings — hit rate undefined.")

    with_address = sum(
        1 for f in filings
        if f.property_address and f.property_address != "Unknown"
        and re.search(r"\d", f.property_address)  # has at least one digit (street number)
    )
    hit_rate = with_address / len(filings)
    assert hit_rate >= 0.60, f"Address hit rate {hit_rate:.0%} below 60% floor"


# --- Live smoke test (opt-in) ---------------------------------------------------

@pytest.mark.skipif(
    os.environ.get("SHELBY_SMOKE") != "1",
    reason="Live smoke test — set SHELBY_SMOKE=1 to run.",
)
def test_live_smoke_returns_at_least_50_filings():
    """Quality gate requires >= 50 filings over a sensible lookback. Live network."""
    scraper = ShelbyTNScraper(lookback_days=14)
    filings = scraper.scrape()
    assert len(filings) >= 50, f"Smoke test returned only {len(filings)} filings"

    # And the live address hit rate must clear 60%.
    with_address = sum(
        1 for f in filings
        if f.property_address and f.property_address != "Unknown"
        and re.search(r"\d", f.property_address)
    )
    hit_rate = with_address / len(filings)
    assert hit_rate >= 0.60, f"Live address hit rate {hit_rate:.0%} below 60% floor"