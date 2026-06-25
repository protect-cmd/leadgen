from __future__ import annotations

"""
Unit tests for scrapers/florida/volusia.py (New County Daily Suits Report).

Mocked tests (no network):
  - test_default_lookback_is_2_days / last_error_none_on_init
  - test_parse_report_keeps_only_evictions
  - test_parse_report_extracts_fields
  - test_clean_litigant_strips_trailing_zip
  - test_normalize_case_number
  - test_parse_report_empty_html

Live smoke test (requires VOLUSIA_SMOKE=1):
  - test_live_smoke_returns_filings
"""

import os
from datetime import date

import pytest

from scrapers.florida.volusia import VolusiaScraper


# Trimmed sample of a real DayCoALLNew report (header + a few rows).
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
</table>
</body></html>
"""


def test_default_lookback_is_2_days():
    assert VolusiaScraper().lookback_days == 2


def test_last_error_none_on_init():
    assert VolusiaScraper().last_error is None


def test_parse_report_keeps_only_evictions():
    filings = VolusiaScraper._parse_report(_SAMPLE_HTML, date(2026, 6, 24))
    # 3 rows in the table, but only 2 are evictions (the Small Claims row drops)
    assert len(filings) == 2
    assert {f.case_number for f in filings} == {"2026-20582-COCI", "2026-20601-COCI"}


def test_parse_report_extracts_fields():
    filings = VolusiaScraper._parse_report(_SAMPLE_HTML, date(2026, 6, 24))
    first = next(f for f in filings if f.case_number == "2026-20582-COCI")
    assert first.tenant_name == "Tarsha Butts" or first.tenant_name == "TARSHA BUTTS"
    assert first.landlord_name == "SPT WAH WEDGEWOOD LLC"   # trailing ZIP stripped
    assert first.filing_date == date(2026, 6, 24)
    assert first.county == "Volusia"
    assert first.state == "FL"
    assert first.property_address == "Unknown"
    assert first.notice_type == "Eviction"


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


def test_parse_report_empty_html():
    assert VolusiaScraper._parse_report("<html><body>no table</body></html>", date(2026, 6, 24)) == []


# ------------------------------------------------------------------ #
#  Live smoke test                                                    #
# ------------------------------------------------------------------ #

SMOKE = os.getenv("VOLUSIA_SMOKE", "0") == "1"


@pytest.mark.skipif(not SMOKE, reason="Set VOLUSIA_SMOKE=1 to run live")
def test_live_smoke_returns_filings():
    """
    Live test: fetches the real daily suits reports.

    Run with:
        VOLUSIA_SMOKE=1 python -m pytest tests/test_volusia_scraper.py::test_live_smoke_returns_filings -v -s
    """
    import asyncio

    scraper = VolusiaScraper(lookback_days=7)
    filings = asyncio.run(scraper.scrape())

    print(f"\n[SMOKE] last_error: {scraper.last_error}")
    print(f"[SMOKE] Total eviction filings: {len(filings)}")
    for f in filings[:10]:
        print(f"  {f.case_number} | {f.tenant_name} | landlord={f.landlord_name} | {f.filing_date}")

    assert scraper.last_error is None, f"scrape failed: {scraper.last_error}"
    assert len(filings) > 0, "Expected at least 1 eviction in a 7-day window"
    for f in filings:
        assert f.state == "FL"
        assert f.county == "Volusia"
        assert f.filing_date is not None
        assert f.tenant_name
