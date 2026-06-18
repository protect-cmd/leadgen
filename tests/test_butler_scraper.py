"""
Tests for scrapers/ohio/butler.py

HTML fixtures are modelled on real docket.bcohio.gov (Henschen & Associates CaseLook PHP).
Confirmed structure from live portal probing on 2026-06-11.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Prevent conftest autouse fixtures from failing when Supabase / SearchBug env
# vars are absent.  The fixtures call `from services import X` which triggers
# module-level code that tries to connect to Supabase / external APIs.
# By pre-inserting stubs into sys.modules here (at test-collection time, which
# is earlier than fixture execution), those imports get the stub instead.
# ---------------------------------------------------------------------------
import sys
from unittest.mock import MagicMock as _MagicMock

_dedup_stub = _MagicMock()
_dedup_stub._reset_run_metrics_columns_cache_for_tests = _MagicMock()
sys.modules.setdefault("services.dedup_service", _dedup_stub)

_searchbug_stub = _MagicMock()
_searchbug_stub.reset_circuit_breaker_for_tests = _MagicMock()
sys.modules.setdefault("services.searchbug_service", _searchbug_stub)
# ---------------------------------------------------------------------------

from datetime import date
from unittest.mock import MagicMock

import pytest

from scrapers.ohio.butler import (
    ButlerCountyAreaCourtScraper,
    _fetch_case_detail,
    _parse_results_page,
    _strip_occupant_suffix,
)


# ---------------------------------------------------------------------------
# HTML fixtures — mirroring real docket.bcohio.gov structure
# ---------------------------------------------------------------------------

# Henschen results page: .record div cards, not a table with /record/ href links.
# CVG = eviction, CRB = criminal, TRC = traffic (should be filtered out).
SAMPLE_RESULTS_HTML = """
<html><body>
<p>3 matches were found (3 displayed)</p>
<div class="record">
  <div class="caseTitle">
    <span class="caseCounter">1</span>
    <span class="fullCaseNumber">CVG2500763</span>
    <span class="agencyName">Area 1</span>
  </div>
  <div class="caseInfo">
    <div class="caseField concerningName">Concerning: Reese, Madison</div>
    <div class="caseField hearingDate">Hearing Date: 06/17/2026</div>
    <div class="caseField caseType">Case Type: Civil</div>
  </div>
  <div class="caseLink">
    <a href="recordSearch.php?k=caseMulti0910KEY1234567890">Case</a>
  </div>
</div>
<div class="record">
  <div class="caseTitle">
    <span class="fullCaseNumber">CVG2600100</span>
    <span class="agencyName">Area 1</span>
  </div>
  <div class="caseInfo">
    <div class="caseField concerningName">Concerning: Doe, Jane et al</div>
    <div class="caseField hearingDate">Hearing Date: 06/20/2026</div>
    <div class="caseField caseType">Case Type: Civil</div>
  </div>
  <div class="caseLink">
    <a href="recordSearch.php?k=caseMulti0910KEY9876543210">Case</a>
  </div>
</div>
<div class="record">
  <div class="caseTitle">
    <span class="fullCaseNumber">CRB2600347</span>
    <span class="agencyName">Area 1</span>
  </div>
  <div class="caseInfo">
    <div class="caseField concerningName">Concerning: Campbell, Radane L</div>
    <div class="caseField hearingDate">Hearing Date: 06/11/2026</div>
    <div class="caseField caseType">Case Type: Criminal</div>
  </div>
  <div class="caseLink">
    <a href="recordSearch.php?k=caseMulti0910KEYCRB0000001">Case</a>
  </div>
</div>
</body></html>
"""

# Results page with pagination (total > 250 displayed)
SAMPLE_RESULTS_PAGINATED_HTML = """
<html><body>
<p>500 matches were found (250 displayed)</p>
<div class="pageNavigation">
  <a href="?k=page0999SESSIONABC123&p=1">2</a>
  <a href="?k=page0999SESSIONABC123&p=3">&gt;&gt;</a>
</div>
<div class="record">
  <div class="caseTitle">
    <span class="fullCaseNumber">CVG2600999</span>
  </div>
  <div class="caseInfo">
    <div class="caseField concerningName">Concerning: Smith, John</div>
    <div class="caseField hearingDate">Hearing Date: 07/01/2026</div>
  </div>
  <div class="caseLink">
    <a href="recordSearch.php?k=caseMulti0910SESSIONABC123CASEID1">Case</a>
  </div>
</div>
</body></html>
"""

# Case detail page — mirrors real Henschen party table layout.
# Defendant row: cells[0]=role, cells[1]=name, cells[5]=street, cells[7]=C/S/Z
# Miscellaneous table: "Filing Date: MM/DD/YYYY" in plain text
SAMPLE_DETAIL_HTML = """
<html><body>
<main>
<h1>Case Information: CVG2500763</h1>
<table>
  <thead>
    <tr>
      <th>Party</th><th>Name</th><th>Attorney</th><th></th><th></th>
      <th>Address</th><th></th><th>C/S/Z</th><th></th><th>Date Served</th><th></th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>Plaintiff 1:\nHazel Valley Homes\nAttorney:\nRobert Friedman</td>
      <td>Hazel Valley Homes</td>
      <td>Robert Friedman</td>
      <td></td><td></td>
      <td>2000 Auburn Dr Ste 200</td>
      <td></td>
      <td>Beachwood, Oh 44122</td>
      <td></td><td></td><td></td>
    </tr>
    <tr>
      <td>Defendant 1:\nReese, Madison\nAttorney:</td>
      <td>Reese, Madison</td>
      <td></td>
      <td></td><td></td>
      <td>1816 Waco Court</td>
      <td></td>
      <td>Hamilton, Oh 45013</td>
      <td></td>
      <td>02/19/2026</td>
      <td></td>
    </tr>
  </tbody>
</table>
<table>
  <tbody>
    <tr><td>Miscellaneous Information</td></tr>
    <tr><td>Hearing Type:</td><td>2ND</td><td>Filing Date:</td><td>11/13/2025</td></tr>
    <tr><td>Hearing Date:</td><td>06/17/2026</td><td>Cause of Action:</td><td>EVICTIONS</td></tr>
    <tr><td>Hearing Time:</td><td>01:15 PM</td><td>Presiding Judge:</td><td>RHL</td></tr>
  </tbody>
</table>
</main>
</body></html>
"""

SAMPLE_DETAIL_HTML_NO_ADDRESS = """
<html><body>
<table>
  <tbody>
    <tr>
      <td>Plaintiff 1:\nSome Landlord LLC</td>
      <td>Some Landlord LLC</td>
      <td></td><td></td><td></td>
      <td></td>
      <td></td>
      <td></td>
      <td></td><td></td><td></td>
    </tr>
    <tr>
      <td>Defendant 1:\nDoe, Jane</td>
      <td>Doe, Jane</td>
      <td></td><td></td><td></td>
      <td></td>
      <td></td>
      <td></td>
      <td></td><td></td><td></td>
    </tr>
  </tbody>
</table>
<table>
  <tbody>
    <tr><td>Filing Date:</td><td>03/01/2026</td></tr>
  </tbody>
</table>
</body></html>
"""

DISCLAIMER_HTML = """
<html><body>
<p>By clicking Continue you agree to the terms.</p>
<a href="/recordSearch.php?k=acceptAgreementsearchForm0999">Continue</a>
</body></html>
"""

TABS_HTML = """
<html><body>
<a href="recordSearch.php?k=searchForm0999SESSKEY123">Case Search</a>
<a href="recordSearch.php?k=docketSearchForm0999SESSKEY123">Calendar Search</a>
</body></html>
"""

CALENDAR_FORM_HTML = """
<html><body>
<form action="/recordSearch.php" method="post">
  <input type="hidden" name="" value="MU">
  <input type="hidden" name="searchType" value="docketDate">
  <input type="hidden" name="k" value="docketSearchForm0999SESSKEY123">
  <select name="searchBMonth"></select>
  <input type="radio" name="searchAgency[]" value="0910"> Area 1
  <input type="submit" value="Begin Search">
</form>
</body></html>
"""

EMPTY_RESULTS_HTML = "<html><body><p>No results.</p></body></html>"


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


def test_strip_occupant_suffix_handles_empty_string():
    assert _strip_occupant_suffix("") == ""


# ---------------------------------------------------------------------------
# _parse_results_page
# ---------------------------------------------------------------------------

def test_parse_results_page_returns_only_cvg_cases():
    stubs, _ = _parse_results_page(SAMPLE_RESULTS_HTML)
    assert len(stubs) == 2
    assert all(s["case_number"].startswith("CVG") for s in stubs)


def test_parse_results_page_maps_case_numbers():
    stubs, _ = _parse_results_page(SAMPLE_RESULTS_HTML)
    assert stubs[0]["case_number"] == "CVG2500763"
    assert stubs[1]["case_number"] == "CVG2600100"


def test_parse_results_page_parses_tenant_name():
    stubs, _ = _parse_results_page(SAMPLE_RESULTS_HTML)
    assert stubs[0]["tenant_raw"] == "Reese, Madison"


def test_parse_results_page_strips_concerning_prefix():
    stubs, _ = _parse_results_page(SAMPLE_RESULTS_HTML)
    # "Concerning: " prefix must be stripped
    assert not stubs[0]["tenant_raw"].startswith("Concerning")


def test_parse_results_page_parses_hearing_date():
    stubs, _ = _parse_results_page(SAMPLE_RESULTS_HTML)
    assert stubs[0]["court_date"] == date(2026, 6, 17)
    assert stubs[1]["court_date"] == date(2026, 6, 20)


def test_parse_results_page_builds_absolute_source_url():
    stubs, _ = _parse_results_page(SAMPLE_RESULTS_HTML)
    assert stubs[0]["source_url"].startswith("https://docket.bcohio.gov")
    assert "caseMulti" in stubs[0]["source_url"]


def test_parse_results_page_extracts_total_count():
    _, total = _parse_results_page(SAMPLE_RESULTS_HTML)
    assert total == 3


def test_parse_results_page_extracts_paginated_total():
    _, total = _parse_results_page(SAMPLE_RESULTS_PAGINATED_HTML)
    assert total == 500


def test_parse_results_page_returns_empty_on_blank_page():
    stubs, total = _parse_results_page(EMPTY_RESULTS_HTML)
    assert stubs == []
    assert total == 0


def test_parse_results_page_tenant_fallback_on_missing_concerning():
    html = """
    <html><body>
    <p>1 match was found</p>
    <div class="record">
      <div class="caseTitle">
        <span class="fullCaseNumber">CVG2600001</span>
      </div>
      <div class="caseInfo">
        <div class="caseField hearingDate">Hearing Date: 06/25/2026</div>
      </div>
      <div class="caseLink"><a href="recordSearch.php?k=caseMulti0910TESTKEY">Case</a></div>
    </div>
    </body></html>
    """
    stubs, _ = _parse_results_page(html)
    assert len(stubs) == 1
    assert stubs[0]["tenant_raw"] == ""


# ---------------------------------------------------------------------------
# _fetch_case_detail
# ---------------------------------------------------------------------------

class TestFetchCaseDetail:
    def _make_session(self, html: str, status: int = 200) -> MagicMock:
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = status
        resp.text = html
        resp.raise_for_status = MagicMock()
        if status >= 400:
            resp.raise_for_status.side_effect = Exception(f"HTTP {status}")
        session.get.return_value = resp
        return session

    def test_returns_landlord_from_plaintiff_row(self):
        session = self._make_session(SAMPLE_DETAIL_HTML)
        landlord, _, _ = _fetch_case_detail(session, "https://docket.bcohio.gov/recordSearch.php?k=test")
        assert landlord == "Hazel Valley Homes"

    def test_returns_address_from_defendant_row(self):
        session = self._make_session(SAMPLE_DETAIL_HTML)
        _, address, _ = _fetch_case_detail(session, "https://docket.bcohio.gov/recordSearch.php?k=test")
        assert address == "1816 Waco Court, Hamilton, Oh 45013"

    def test_returns_filing_date_from_miscellaneous_table(self):
        session = self._make_session(SAMPLE_DETAIL_HTML)
        _, _, filing_date = _fetch_case_detail(session, "https://docket.bcohio.gov/recordSearch.php?k=test")
        assert filing_date == date(2025, 11, 13)

    def test_returns_none_address_when_cells_empty(self):
        session = self._make_session(SAMPLE_DETAIL_HTML_NO_ADDRESS)
        _, address, _ = _fetch_case_detail(session, "https://docket.bcohio.gov/recordSearch.php?k=test")
        assert address is None

    def test_returns_none_on_http_error(self):
        session = self._make_session("<html></html>", status=404)
        landlord, address, filing_date = _fetch_case_detail(
            session, "https://docket.bcohio.gov/recordSearch.php?k=bad"
        )
        assert landlord is None
        assert address is None
        assert filing_date is None

    def test_returns_none_on_exception(self):
        session = MagicMock()
        session.get.side_effect = ConnectionError("timeout")
        landlord, address, filing_date = _fetch_case_detail(
            session, "https://docket.bcohio.gov/recordSearch.php?k=bad"
        )
        assert landlord is None
        assert address is None
        assert filing_date is None

    def test_fallback_regex_parsing_when_table_empty(self):
        """_fetch_case_detail falls back to text regex if table cells are empty."""
        html = """
        <html><body>
        <table><tbody>
          <tr>
            <td>Defendant 1:\nWilson, Robert</td>
            <td>Wilson, Robert</td>
            <td></td><td></td><td></td>
            <td></td><td></td><td></td>
            <td></td><td></td><td></td>
          </tr>
        </tbody></table>
        <p>Address: 900 Pine St</p>
        <p>C/S/Z: Dayton, Oh 45402</p>
        <p>Filing Date: 05/05/2026</p>
        </body></html>
        """
        session = self._make_session(html)
        _, address, filing_date = _fetch_case_detail(
            session, "https://docket.bcohio.gov/recordSearch.php?k=test"
        )
        # Regex fallback should find address and filing date in page text
        assert address is not None
        assert "900 Pine St" in address
        assert filing_date == date(2026, 5, 5)


# ---------------------------------------------------------------------------
# ButlerCountyAreaCourtScraper._ensure_session  (mocked HTTP)
# ---------------------------------------------------------------------------

class TestEnsureSession:
    def _make_session_with_responses(self, *html_pages: str) -> MagicMock:
        """Return a mock requests.Session that returns each html_page in order."""
        session = MagicMock()
        responses = []
        for html in html_pages:
            resp = MagicMock()
            resp.status_code = 200
            resp.text = html
            resp.raise_for_status = MagicMock()
            resp.url = "https://docket.bcohio.gov/recordSearch.php"
            responses.append(resp)
        session.get.side_effect = responses
        return session

    def test_returns_form_k_on_success(self, monkeypatch):
        scraper = ButlerCountyAreaCourtScraper()
        mock_sess = self._make_session_with_responses(
            DISCLAIMER_HTML, TABS_HTML, CALENDAR_FORM_HTML
        )
        monkeypatch.setattr(scraper, "session", mock_sess)
        form_k = scraper._ensure_session()
        assert form_k == "docketSearchForm0999SESSKEY123"

    def test_returns_none_when_no_accept_link(self, monkeypatch):
        scraper = ButlerCountyAreaCourtScraper()
        mock_sess = self._make_session_with_responses("<html><body>No link</body></html>")
        monkeypatch.setattr(scraper, "session", mock_sess)
        form_k = scraper._ensure_session()
        assert form_k is None

    def test_returns_none_when_no_calendar_tab(self, monkeypatch):
        scraper = ButlerCountyAreaCourtScraper()
        tabs_no_cal = "<html><body><a href='?k=searchForm0999X'>Case Search</a></body></html>"
        mock_sess = self._make_session_with_responses(DISCLAIMER_HTML, tabs_no_cal)
        monkeypatch.setattr(scraper, "session", mock_sess)
        form_k = scraper._ensure_session()
        assert form_k is None

    def test_returns_none_on_network_error(self, monkeypatch):
        scraper = ButlerCountyAreaCourtScraper()
        mock_sess = MagicMock()
        mock_sess.get.side_effect = ConnectionError("refused")
        monkeypatch.setattr(scraper, "session", mock_sess)
        form_k = scraper._ensure_session()
        assert form_k is None


# ---------------------------------------------------------------------------
# ButlerCountyAreaCourtScraper.scrape  (monkeypatched internals)
# ---------------------------------------------------------------------------

FORM_K = "docketSearchForm0999SESSKEY123"


def test_scrape_returns_empty_and_sets_last_error_when_session_fails(monkeypatch):
    scraper = ButlerCountyAreaCourtScraper()
    monkeypatch.setattr(scraper, "_ensure_session", lambda: None)
    filings = scraper.scrape()
    assert filings == []
    assert scraper.last_error is not None


def test_scrape_returns_filings_across_all_three_areas(monkeypatch):
    """_scrape_area is called once per area; results are aggregated and deduplicated."""
    scraper = ButlerCountyAreaCourtScraper()
    monkeypatch.setattr(scraper, "_ensure_session", lambda: FORM_K)

    from models.filing import Filing
    from datetime import date as _date

    def make_filing(case_num):
        return Filing(
            case_number=case_num,
            tenant_name="Tenant",
            property_address="123 Main St",
            landlord_name="Landlord LLC",
            filing_date=_date(2026, 1, 1),
            court_date=_date(2026, 6, 15),
            state="OH",
            county="Butler",
            notice_type="Eviction",
            source_url="https://docket.bcohio.gov/recordSearch.php?k=test",
        )

    area_results = {
        "0910": [make_filing("CVG2600001")],
        "0911": [make_filing("CVG2600002")],
        "0912": [make_filing("CVG2600003")],
    }

    def mock_scrape_area(form_k, area_code, start, end, today):
        return area_results[area_code]

    monkeypatch.setattr(scraper, "_scrape_area", mock_scrape_area)

    filings = scraper.scrape()
    assert len(filings) == 3
    assert {f.case_number for f in filings} == {"CVG2600001", "CVG2600002", "CVG2600003"}
    assert scraper.last_error is None


def test_scrape_deduplicates_cases_appearing_in_multiple_areas(monkeypatch):
    """Same case_number from two areas must only appear once."""
    scraper = ButlerCountyAreaCourtScraper()
    monkeypatch.setattr(scraper, "_ensure_session", lambda: FORM_K)

    from models.filing import Filing
    from datetime import date as _date

    shared_filing = Filing(
        case_number="CVG2600001",
        tenant_name="Tenant",
        property_address="123 Main St",
        landlord_name="Landlord",
        filing_date=_date(2026, 1, 1),
        court_date=_date(2026, 6, 15),
        state="OH",
        county="Butler",
        notice_type="Eviction",
        source_url="https://docket.bcohio.gov/recordSearch.php?k=test",
    )

    monkeypatch.setattr(
        scraper,
        "_scrape_area",
        lambda form_k, area_code, start, end, today: [shared_filing],
    )

    filings = scraper.scrape()
    assert len(filings) == 1


def test_scrape_continues_when_one_area_raises(monkeypatch):
    """An exception in one area does not prevent the others from running."""
    scraper = ButlerCountyAreaCourtScraper()
    monkeypatch.setattr(scraper, "_ensure_session", lambda: FORM_K)

    from models.filing import Filing
    from datetime import date as _date

    call_order = []

    def mock_scrape_area(form_k, area_code, start, end, today):
        call_order.append(area_code)
        if area_code == "0911":
            raise RuntimeError("area 2 failed")
        return [
            Filing(
                case_number=f"CVG{area_code}01",
                tenant_name="T",
                property_address="A",
                landlord_name="L",
                filing_date=_date(2026, 1, 1),
                court_date=_date(2026, 6, 15),
                state="OH",
                county="Butler",
                notice_type="Eviction",
                source_url="https://docket.bcohio.gov/recordSearch.php?k=test",
            )
        ]

    monkeypatch.setattr(scraper, "_scrape_area", mock_scrape_area)

    filings = scraper.scrape()
    assert len(call_order) == 3  # all three areas attempted
    assert len(filings) == 2     # area 0911 failed, 0910 and 0912 succeeded


def test_scrape_sets_last_error_when_all_areas_fail(monkeypatch):
    """If every area raises, last_error must be set (not silently swallowed)."""
    scraper = ButlerCountyAreaCourtScraper()
    monkeypatch.setattr(scraper, "_ensure_session", lambda: FORM_K)

    def raise_for_all(form_k, area_code, start, end, today):
        raise RuntimeError(f"area {area_code} blocked")

    monkeypatch.setattr(scraper, "_scrape_area", raise_for_all)

    filings = scraper.scrape()

    assert filings == []
    assert scraper.last_error is not None
    assert "blocked" in scraper.last_error
