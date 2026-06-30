from __future__ import annotations

"""
Unit tests for scrapers/florida/volusia.py (New County Daily Suits Report +
CCMS per-case address enrichment).

Mocked tests (no network):
  - test_default_lookback_is_2_days / test_last_error_none_on_init
  - test_parse_report_keeps_only_evictions
  - test_parse_report_extracts_fields
  - test_parse_report_junk_name_becomes_unknown (regression: no placeholder re-injection)
  - test_clean_litigant_strips_trailing_zip
  - test_normalize_case_number
  - test_parse_report_empty_html
  - test_parse_aspnet_hidden
  - test_extract_first_detail_url
  - test_parse_defendant_address_table_row
  - test_parse_defendant_address_fallback_regex
  - test_scrape_enriches_addresses (mocked end-to-end)
  - test_scrape_sets_last_error_on_index_failure

Live smoke test (requires VOLUSIA_SMOKE=1, US IP):
  - test_live_smoke_returns_filings_with_addresses
"""

import os
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from scrapers.florida.volusia import (
    VolusiaScraper,
    _extract_first_detail_url,
    _parse_aspnet_hidden,
    _parse_defendant_address,
)


# ---------------------------------------------------------------------------
# Shared fixture HTML
# ---------------------------------------------------------------------------

_SAMPLE_HTML = """
<html><body>
<table>
<tr><th>Case Number</th><th>Div</th><th>Primary Litigant #1</th>
    <th>Primary Litigant #2</th><th>Category</th></tr>
<tr><td>2026 20582 COCI</td><td>84</td>
    <td>SPT WAH WEDGEWOOD LLC&nbsp;&nbsp;&nbsp;32117</td>
    <td>TARSHA BUTTS</td><td>Eviction</td></tr>
<tr><td>2026 20583 COCI</td><td>84</td>
    <td>PORTFOLIO RECOVERY ASSOCIATES LLC 23502 4952</td>
    <td>SCOTT HOWELL</td><td>Small Claims $2,501 up to/incl $8,000</td></tr>
<tr><td>2026 20601 COCI</td><td>84</td>
    <td>INDIGO DEKALB TIC LLC AND INDIGO DEAN TIC LLC 33606-4121</td>
    <td>ANTHONY GLENN</td><td>Eviction</td></tr>
<tr><td>2026 20610 COCI</td><td>84</td>
    <td>PALM GROVE LLC 32114</td>
    <td>JOHN DOE</td><td>Eviction</td></tr>
</table>
</body></html>
"""

_CCMS_SEARCH_HTML = """
<html><body>
<form method="post">
<input type="hidden" name="__VIEWSTATE" value="abc123" />
<input type="hidden" name="__EVENTVALIDATION" value="def456" />
<input type="hidden" name="__VIEWSTATEGENERATOR" value="ghi789" />
<a href="CaseDetail.aspx?CaseID=99999">2026-20582-COCI</a>
</form>
</body></html>
"""

_CCMS_DETAIL_HTML = """
<html><body>
<table>
  <tr><td>Plaintiff</td><td>PALM GROVE LLC</td><td>123 BUSINESS RD, ORLANDO, FL 32801</td></tr>
  <tr><td>Defendant</td><td>JOHN DOE</td><td>456 OAK AVE, DAYTONA BEACH, FL 32114</td></tr>
</table>
</body></html>
"""

_CCMS_DETAIL_HTML_FALLBACK = """
<html><body>
<p>Party: JOHN DOE  Address: 789 PINE ST APT 2, DELAND, FL 32720</p>
</body></html>
"""


# ---------------------------------------------------------------------------
# Basic scraper attribute tests
# ---------------------------------------------------------------------------

def test_default_lookback_is_2_days():
    assert VolusiaScraper().lookback_days == 2


def test_last_error_none_on_init():
    assert VolusiaScraper().last_error is None


# ---------------------------------------------------------------------------
# _parse_report
# ---------------------------------------------------------------------------

def test_parse_report_keeps_only_evictions():
    filings = VolusiaScraper._parse_report(_SAMPLE_HTML, date(2026, 6, 24))
    # 4 rows: 3 evictions, 1 Small Claims
    assert len(filings) == 3
    case_nums = {f.case_number for f in filings}
    assert case_nums == {"2026-20582-COCI", "2026-20601-COCI", "2026-20610-COCI"}


def test_parse_report_extracts_fields():
    filings = VolusiaScraper._parse_report(_SAMPLE_HTML, date(2026, 6, 24))
    first = next(f for f in filings if f.case_number == "2026-20582-COCI")
    assert first.tenant_name not in ("", None)
    assert "BUTTS" in first.tenant_name.upper() or "Butts" in first.tenant_name
    assert first.landlord_name == "SPT WAH WEDGEWOOD LLC"
    assert first.filing_date == date(2026, 6, 24)
    assert first.county == "Volusia"
    assert first.state == "FL"
    # property_address starts as "Unknown"; enriched later by CCMS
    assert first.property_address == "Unknown"
    assert first.notice_type == "Eviction"


def test_parse_report_junk_name_becomes_unknown():
    """Placeholder names must become 'Unknown', not be re-injected from tenant_raw."""
    filings = VolusiaScraper._parse_report(_SAMPLE_HTML, date(2026, 6, 24))
    doe_filing = next(f for f in filings if f.case_number == "2026-20610-COCI")
    assert doe_filing.tenant_name == "Unknown"


def test_parse_report_empty_html():
    assert VolusiaScraper._parse_report("<html><body>no table</body></html>", date(2026, 6, 24)) == []


# ---------------------------------------------------------------------------
# _clean_litigant / _normalize_case_number
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("SPT WAH WEDGEWOOD LLC   32117", "SPT WAH WEDGEWOOD LLC"),
        ("INDIGO DEKALB TIC LLC 33606-4121", "INDIGO DEKALB TIC LLC"),
        ("SOME OWNER LLC", "SOME OWNER LLC"),
        ("OWNER, 32114", "OWNER"),
    ],
)
def test_clean_litigant_strips_trailing_zip(raw, expected):
    assert VolusiaScraper._clean_litigant(raw) == expected


def test_normalize_case_number():
    assert VolusiaScraper._normalize_case_number("2026 20582 COCI") == "2026-20582-COCI"


# ---------------------------------------------------------------------------
# CCMS helpers
# ---------------------------------------------------------------------------

def test_parse_aspnet_hidden():
    vs, ev, gen = _parse_aspnet_hidden(_CCMS_SEARCH_HTML)
    assert vs == "abc123"
    assert ev == "def456"
    assert gen == "ghi789"


def test_parse_aspnet_hidden_missing_fields():
    vs, ev, gen = _parse_aspnet_hidden("<html></html>")
    assert vs == ev == gen == ""


def test_extract_first_detail_url():
    url = _extract_first_detail_url(_CCMS_SEARCH_HTML)
    assert url == "https://ccms.clerk.org/CaseDetail.aspx?CaseID=99999"


def test_extract_first_detail_url_no_match():
    assert _extract_first_detail_url("<html>no links here</html>") is None


def test_parse_defendant_address_table_row():
    addr = _parse_defendant_address(_CCMS_DETAIL_HTML)
    assert addr == "456 OAK AVE, DAYTONA BEACH, FL 32114"


def test_parse_defendant_address_fallback_regex():
    addr = _parse_defendant_address(_CCMS_DETAIL_HTML_FALLBACK)
    assert addr == "789 PINE ST APT 2, DELAND, FL 32720"


def test_parse_defendant_address_no_match():
    assert _parse_defendant_address("<html><body>No address here.</body></html>") is None


# ---------------------------------------------------------------------------
# scrape() — mocked end-to-end: verifies address enrichment integrates
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scrape_enriches_addresses():
    """Happy-path mock: day report returns evictions; CCMS returns an address."""
    import httpx
    from datetime import date as _date

    async def _mock_get(self_client, url, **kwargs):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.status_code = 200
        if "inquirySU" in url:
            resp.text = "DayCoALLNew_2026_06_24.html"
        elif "DayCoALLNew" in url:
            resp.text = _SAMPLE_HTML
        elif "CaseSearch" in url:
            resp.text = _CCMS_SEARCH_HTML
        elif "CaseDetail" in url:
            resp.text = _CCMS_DETAIL_HTML
        else:
            resp.text = ""
        return resp

    async def _mock_post(self_client, url, **kwargs):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.status_code = 200
        resp.text = _CCMS_SEARCH_HTML
        return resp

    with patch("scrapers.florida.volusia.court_today", return_value=_date(2026, 6, 24)):
        scraper = VolusiaScraper(lookback_days=1)
        with patch.object(httpx.AsyncClient, "get", new=_mock_get):
            with patch.object(httpx.AsyncClient, "post", new=_mock_post):
                filings = await scraper.scrape()

    assert scraper.last_error is None
    addresses = {f.property_address for f in filings}
    assert any(a != "Unknown" for a in addresses), (
        f"Expected at least one resolved address; got: {addresses}"
    )


# ---------------------------------------------------------------------------
# last_error set on index failure
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scrape_sets_last_error_on_index_failure():
    import httpx
    from datetime import date as _date

    async def _failing_get(self_client, url, **kwargs):
        raise httpx.ConnectError("geo-blocked")

    with patch("scrapers.florida.volusia.court_today", return_value=_date(2026, 6, 24)):
        scraper = VolusiaScraper(lookback_days=1)
        with patch.object(httpx.AsyncClient, "get", new=_failing_get):
            filings = await scraper.scrape()

    assert filings == []
    assert scraper.last_error is not None
    assert "index fetch failed" in scraper.last_error


# ---------------------------------------------------------------------------
# Live smoke test (requires VOLUSIA_SMOKE=1, US IP)
# ---------------------------------------------------------------------------

SMOKE = os.getenv("VOLUSIA_SMOKE", "0") == "1"


@pytest.mark.skipif(not SMOKE, reason="Set VOLUSIA_SMOKE=1 to run live")
@pytest.mark.asyncio
async def test_live_smoke_returns_filings_with_addresses():
    """
    Live test — fetches real daily suits reports + CCMS address enrichment.
    Requires a US IP (app02.clerk.org geo-blocks non-US).

    Run with:
        VOLUSIA_SMOKE=1 python -m pytest tests/test_volusia_scraper.py::test_live_smoke_returns_filings_with_addresses -v -s
    """
    scraper = VolusiaScraper(lookback_days=7)
    filings = await scraper.scrape()

    print(f"\n[SMOKE] last_error: {scraper.last_error}")
    print(f"[SMOKE] Total eviction filings: {len(filings)}")
    resolved = [f for f in filings if f.property_address != "Unknown"]
    print(f"[SMOKE] Address-resolved: {len(resolved)}/{len(filings)}")
    for f in filings[:10]:
        print(
            f"  {f.case_number} | {f.tenant_name} | "
            f"addr={f.property_address} | filed={f.filing_date}"
        )

    assert scraper.last_error is None, f"scrape failed: {scraper.last_error}"
    assert len(filings) > 0, "Expected at least 1 eviction in a 7-day window"
    assert len(resolved) > 0, "Expected at least some addresses resolved from CCMS"
    for f in filings:
        assert f.state == "FL"
        assert f.county == "Volusia"
        assert f.filing_date is not None
        assert f.tenant_name
