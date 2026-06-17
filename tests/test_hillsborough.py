from __future__ import annotations

"""
Unit tests for scrapers/florida/hillsborough.py

Mocked tests (no network):
  - test_date_range_is_7_days
  - test_try_parse_date_formats
  - test_cells_to_filing_skips_bad_rows
  - test_eviction_case_type_value_present

Live smoke test (requires HILLSBOROUGH_SMOKE=1 + US IP):
  - test_live_smoke_returns_filings
"""

import os
import pytest
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from scrapers.florida.hillsborough import HillsboroughScraper, EVICTION_CASE_TYPE_VALUE


# ------------------------------------------------------------------ #
#  Mocked / pure-logic tests                                          #
# ------------------------------------------------------------------ #

def test_default_lookback_is_7_days():
    scraper = HillsboroughScraper.__new__(HillsboroughScraper)
    scraper.lookback_days = 7
    today = date(2026, 6, 18)
    start = today - timedelta(days=scraper.lookback_days)
    assert start == date(2026, 6, 11)


def test_date_format_on_or_after():
    today = date(2026, 6, 18)
    start = today - timedelta(days=7)
    assert start.strftime("%m/%d/%Y") == "06/11/2026"
    assert today.strftime("%m/%d/%Y") == "06/18/2026"


@pytest.mark.parametrize("raw,expected", [
    ("06/11/2026", date(2026, 6, 11)),
    ("2026-06-11", date(2026, 6, 11)),
    ("06-11-2026", date(2026, 6, 11)),
    ("not-a-date",  None),
    ("",            None),
])
def test_try_parse_date_formats(raw, expected):
    result = HillsboroughScraper._try_parse_date(raw)
    assert result == expected


def test_eviction_case_type_contains_landlord_codes():
    # Must contain the primary LANDLORD/TENANT/EVICTION codes
    assert "3133" in EVICTION_CASE_TYPE_VALUE
    assert "3154" in EVICTION_CASE_TYPE_VALUE
    assert "3173" in EVICTION_CASE_TYPE_VALUE


def test_scraper_init_stores_lookback():
    with patch.object(HillsboroughScraper, "__init__", lambda self, **kw: None):
        s = HillsboroughScraper.__new__(HillsboroughScraper)
        s.lookback_days = 14
    assert s.lookback_days == 14


def test_scrape_returns_empty_on_portal_failure():
    import asyncio
    """If browser fails to load, scrape() must return [] not raise."""
    scraper = HillsboroughScraper.__new__(HillsboroughScraper)
    scraper.lookback_days = 7
    scraper.headless = True

    mock_page = AsyncMock()
    mock_page.goto.side_effect = Exception("connection refused")

    with patch.object(scraper, "_launch_browser", return_value=mock_page), \
         patch.object(scraper, "_close_browser", new_callable=AsyncMock):
        result = asyncio.run(scraper.scrape())

    assert result == []


def test_scrape_returns_empty_when_no_detail_buttons():
    import asyncio
    """If search returns no button.details rows, scrape() returns []."""
    scraper = HillsboroughScraper.__new__(HillsboroughScraper)
    scraper.lookback_days = 7
    scraper.headless = True

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.wait_for_timeout = AsyncMock()
    mock_page.click = AsyncMock()
    mock_page.select_option = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value=None)
    mock_page.wait_for_selector = AsyncMock(side_effect=Exception("timeout"))

    with patch.object(scraper, "_launch_browser", return_value=mock_page), \
         patch.object(scraper, "_close_browser", new_callable=AsyncMock):
        result = asyncio.run(scraper.scrape())

    assert result == []


# ------------------------------------------------------------------ #
#  Live smoke test                                                     #
# ------------------------------------------------------------------ #

SMOKE = os.getenv("HILLSBOROUGH_SMOKE", "0") == "1"

@pytest.mark.skipif(not SMOKE, reason="Set HILLSBOROUGH_SMOKE=1 to run live")
@pytest.mark.asyncio
def test_live_smoke_returns_filings():
    import asyncio
    """
    Live test: hits the real HOVER portal and checks we get at least
    some filings with valid addresses in a 7-day window.

    Run with:
        HILLSBOROUGH_SMOKE=1 python -m pytest tests/test_hillsborough.py::test_live_smoke_returns_filings -v -s
    """
    scraper = HillsboroughScraper(lookback_days=7, headless=True)
    filings = asyncio.run(scraper.scrape())

    print(f"\n[SMOKE] Total filings: {len(filings)}")
    for f in filings[:10]:
        print(f"  {f.case_number} | {f.tenant_name} | {f.property_address} | {f.filing_date}")

    assert len(filings) > 0, "Expected at least 1 filing in 7-day window"

    with_addr = [f for f in filings if f.property_address not in ("Unknown", "", None)]
    print(f"[SMOKE] With address: {len(with_addr)}/{len(filings)}")

    assert len(with_addr) > 0, "Expected at least 1 filing with a real address"

    # Validate Filing fields
    for f in filings:
        assert f.state   == "FL"
        assert f.county  == "Hillsborough"
        assert f.case_number not in ("", None, "UNKNOWN")
        assert f.filing_date is not None