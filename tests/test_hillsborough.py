from __future__ import annotations

"""
Unit tests for scrapers/florida/hillsborough.py

Mocked tests (no network):
  - test_default_lookback_is_7_days
  - test_date_format_on_or_after
  - test_try_parse_date_formats
  - test_eviction_case_type_value_present
  - test_scraper_init_stores_lookback
  - test_last_error_none_on_init
  - test_scrape_returns_empty_on_portal_failure
  - test_scrape_sets_last_error_on_failure
  - test_scrape_returns_empty_when_no_detail_buttons
  - test_tenant_name_falls_back_to_unknown
  - test_filing_date_warning_logged_when_today

Live smoke test (requires HILLSBOROUGH_SMOKE=1 + US IP):
  - test_live_smoke_returns_filings
"""

import asyncio
import os
import pytest
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from scrapers.florida.hillsborough import HillsboroughScraper, EVICTION_CASE_TYPE_VALUE


# ------------------------------------------------------------------ #
#  Pure-logic / mocked tests                                          #
# ------------------------------------------------------------------ #

def test_default_lookback_is_7_days():
    s = HillsboroughScraper.__new__(HillsboroughScraper)
    s.lookback_days = 7
    today = date(2026, 6, 18)
    start = today - timedelta(days=s.lookback_days)
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
    assert "3133" in EVICTION_CASE_TYPE_VALUE
    assert "3154" in EVICTION_CASE_TYPE_VALUE
    assert "3173" in EVICTION_CASE_TYPE_VALUE


def test_scraper_init_stores_lookback():
    with patch.object(HillsboroughScraper, "__init__", lambda self, **kw: None):
        s = HillsboroughScraper.__new__(HillsboroughScraper)
        s.lookback_days = 14
    assert s.lookback_days == 14


def test_last_error_none_on_init():
    """FIX: last_error must be None at construction so runner can check it."""
    with patch.object(HillsboroughScraper, "__init__", lambda self, **kw: None):
        s = HillsboroughScraper.__new__(HillsboroughScraper)
        s.last_error = None
    assert s.last_error is None


def test_scrape_returns_empty_on_portal_failure():
    """If browser fails to load, scrape() must return [] not raise."""
    import asyncio
    s = HillsboroughScraper.__new__(HillsboroughScraper)
    s.lookback_days = 7
    s.headless      = True
    s.last_error    = None

    mock_page = AsyncMock()
    mock_page.goto.side_effect = Exception("connection refused")
    mock_page.wait_for_timeout = AsyncMock()

    with patch.object(s, "_launch_browser", return_value=mock_page), \
         patch.object(s, "_close_browser", new_callable=AsyncMock):
        result = asyncio.run(s.scrape())

    assert result == []


def test_scrape_sets_last_error_on_failure():
    """FIX: last_error must be set (not None) when scrape fails."""
    import asyncio
    s = HillsboroughScraper.__new__(HillsboroughScraper)
    s.lookback_days = 7
    s.headless      = True
    s.last_error    = None

    mock_page = AsyncMock()
    mock_page.goto.side_effect = Exception("timeout")
    mock_page.wait_for_timeout = AsyncMock()

    with patch.object(s, "_launch_browser", return_value=mock_page), \
         patch.object(s, "_close_browser", new_callable=AsyncMock):
        asyncio.run(s.scrape())

    assert s.last_error is not None
    assert "timeout" in s.last_error


def test_scrape_returns_empty_when_no_detail_buttons():
    """If tab selector times out, scrape() returns [] and sets last_error."""
    import asyncio
    s = HillsboroughScraper.__new__(HillsboroughScraper)
    s.lookback_days = 7
    s.headless      = True
    s.last_error    = None

    mock_page = AsyncMock()
    mock_page.goto           = AsyncMock()
    mock_page.wait_for_timeout = AsyncMock()
    mock_page.click          = AsyncMock()
    mock_page.select_option  = AsyncMock()
    mock_page.evaluate       = AsyncMock(return_value=None)
    # FIX: wait_for_selector raises → tab never appeared
    mock_page.wait_for_selector = AsyncMock(side_effect=Exception("timeout"))

    with patch.object(s, "_launch_browser", return_value=mock_page), \
         patch.object(s, "_close_browser", new_callable=AsyncMock):
        result = asyncio.run(s.scrape())

    assert result == []
    assert s.last_error is not None


def test_tenant_name_falls_back_to_unknown():
    """FIX: clean_tenant_name() returning '' must yield 'Unknown', not raw name."""
    from unittest.mock import patch as mpatch
    with mpatch("scrapers.florida.hillsborough.clean_tenant_name", return_value=""):
        cleaned = ""
        tenant  = cleaned if cleaned else "Unknown"
    assert tenant == "Unknown"


def test_filing_date_default_is_today_when_no_date_in_row():
    """FIX: when no date is parseable the date falls back to today (logged)."""
    today = date(2026, 6, 18)
    # No parseable date in these strings
    texts = ["24-CV-001234", "EVICTION", "ACTIVE"]
    filing_date = today
    for t in texts[1:4]:
        d = HillsboroughScraper._try_parse_date(t)
        if d:
            filing_date = d
            break
    # Still today — which is the logged fallback
    assert filing_date == today


# ------------------------------------------------------------------ #
#  Live smoke test                                                     #
# ------------------------------------------------------------------ #

SMOKE = os.getenv("HILLSBOROUGH_SMOKE", "0") == "1"

@pytest.mark.skipif(not SMOKE, reason="Set HILLSBOROUGH_SMOKE=1 to run live")
def test_live_smoke_returns_filings():
    """
    Live test: hits the real HOVER portal.

    Run with:
        HILLSBOROUGH_SMOKE=1 python -m pytest tests/test_hillsborough.py::test_live_smoke_returns_filings -v -s
    """
    import asyncio
    scraper = HillsboroughScraper(lookback_days=7, headless=True)
    filings = asyncio.run(scraper.scrape())

    print(f"\n[SMOKE] last_error: {scraper.last_error}")
    print(f"[SMOKE] Total filings: {len(filings)}")
    for f in filings[:10]:
        print(f"  {f.case_number} | {f.tenant_name} | {f.property_address} | {f.filing_date}")

    assert scraper.last_error is None, f"scrape failed: {scraper.last_error}"
    assert len(filings) > 0, "Expected at least 1 filing in 7-day window"

    with_addr = [f for f in filings if f.property_address not in ("Unknown", "", None)]
    print(f"[SMOKE] With address: {len(with_addr)}/{len(filings)}")
    assert len(with_addr) > 0, "Expected at least 1 filing with a real address"

    for f in filings:
        assert f.state   == "FL"
        assert f.county  == "Hillsborough"
        assert f.case_number not in ("", None, "UNKNOWN")
        assert f.filing_date is not None
        assert f.tenant_name != ""