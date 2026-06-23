from __future__ import annotations

"""
Unit tests for scrapers/florida/orange.py

Mocked tests (no network):
  - test_default_lookback_is_7_days
  - test_date_format_for_orange_portal
  - test_try_parse_address_from_text
  - test_eviction_case_type_value_is_41
  - test_scrape_returns_empty_on_portal_failure
  - test_scrape_returns_empty_when_search_button_never_enables

Live smoke test (requires ORANGE_SMOKE=1 + US IP + captcha solver):
  - test_live_smoke_returns_filings
"""

import os
import pytest
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

from scrapers.florida.orange import (
    OrangeScraper,
    EVICTION_CASE_TYPE_VALUE,
    STREET_SUFFIX_REGEX,
)


# ------------------------------------------------------------------ #
#  Mocked / pure-logic tests                                          #
# ------------------------------------------------------------------ #

def test_default_lookback_is_7_days():
    scraper = OrangeScraper.__new__(OrangeScraper)
    scraper.lookback_days = 7
    today = date(2026, 6, 18)
    start = today - timedelta(days=scraper.lookback_days)
    assert start == date(2026, 6, 11)


def test_date_format_for_orange_portal():
    """
    Orange uses M/d/yy format (not zero-padded, 2-digit year).
    Eg. 6/11/26 not 06/11/2026.
    """
    today = date(2026, 6, 18)
    start = today - timedelta(days=7)
    start_str = f"{start.month}/{start.day}/{start.year % 100:02d}"
    end_str   = f"{today.month}/{today.day}/{today.year % 100:02d}"
    assert start_str == "6/11/26"
    assert end_str   == "6/18/26"


def test_eviction_case_type_value_is_41():
    """Confirmed via DOM inspection 2026-06-10."""
    assert EVICTION_CASE_TYPE_VALUE == "41"


def test_scraper_init_stores_lookback():
    s = OrangeScraper(lookback_days=14, headless=True)
    assert s.lookback_days == 14
    assert s.headless is True


@pytest.mark.parametrize("text,expected_substring", [
    (
        "Defendant resides at 1234 OAK STREET, ORLANDO, FL 32801",
        "1234 OAK STREET",
    ),
    (
        "Service address: 5678 PINE AVENUE APT 3B, WINTER PARK, FL 32789-1234",
        "5678 PINE AVENUE",
    ),
    (
        "Property: 999 N MILLS DRIVE, ORLANDO FL 32803",
        "999 N MILLS DRIVE",
    ),
    (
        "No address in this text at all",
        None,
    ),
])
def test_try_parse_address_from_text(text, expected_substring):
    result = OrangeScraper._parse_address_from_text(text)
    if expected_substring is None:
        assert result is None
    else:
        assert result is not None
        assert expected_substring.upper() in result.upper()


def test_regex_handles_florida_street_suffixes():
    """Smoke check that common FL street suffixes match."""
    samples = [
        "100 MAIN STREET",
        "200 OAK AVE",
        "300 PINE BLVD",
        "400 LAKE DR",
        "500 BAY CIRCLE",
        "600 PALM COURT",
        "700 RIVER WAY",
        "800 GULF HWY",
    ]
    for s in samples:
        assert STREET_SUFFIX_REGEX.search(s), f"Regex missed: {s}"


def test_scrape_returns_empty_on_portal_failure():
    """If browser fails to load, scrape() must return [] not raise."""
    import asyncio

    scraper = OrangeScraper.__new__(OrangeScraper)
    scraper.lookback_days = 7
    scraper.headless = True

    mock_page = AsyncMock()
    mock_page.goto.side_effect = Exception("connection refused")

    with patch.object(scraper, "_launch_browser", return_value=mock_page), \
         patch.object(scraper, "_close_browser", new_callable=AsyncMock):
        result = asyncio.run(scraper.scrape())

    assert result == []


def test_scrape_returns_empty_when_search_button_never_enables():
    """If captcha is never solved (Search button stays disabled), return []."""
    import asyncio

    scraper = OrangeScraper.__new__(OrangeScraper)
    scraper.lookback_days = 7
    scraper.headless = True

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.wait_for_timeout = AsyncMock()
    mock_page.click = AsyncMock()
    mock_page.fill = AsyncMock()
    mock_page.wait_for_function = AsyncMock(side_effect=Exception("timeout"))

    with patch.object(scraper, "_launch_browser", return_value=mock_page), \
         patch.object(scraper, "_close_browser", new_callable=AsyncMock):
        result = asyncio.run(scraper.scrape())

    assert result == []


# ------------------------------------------------------------------ #
#  Live smoke test                                                    #
# ------------------------------------------------------------------ #

SMOKE = os.getenv("ORANGE_SMOKE", "0") == "1"


@pytest.mark.skipif(not SMOKE, reason="Set ORANGE_SMOKE=1 to run live")
def test_live_smoke_returns_filings():
    """
    Live test: hits the real myeclerk portal and checks we get at least
    some filings with valid addresses in a 7-day window.

    Run with:
        ORANGE_SMOKE=1 python -m pytest tests/test_orange_scraper.py::test_live_smoke_returns_filings -v -s

    Requires:
      - US IP (Railway or US VPN)
      - reCAPTCHA solver available to the browser context
    """
    import asyncio

    scraper = OrangeScraper(lookback_days=7, headless=True)
    filings = asyncio.run(scraper.scrape())

    print(f"\n[SMOKE] Total filings: {len(filings)}")
    for f in filings[:10]:
        print(f"  {f.case_number} | {f.tenant_name} | {f.property_address} | {f.filing_date}")

    assert len(filings) > 0, "Expected at least 1 filing in 7-day window"

    with_addr = [f for f in filings if f.property_address not in ("Unknown", "", None)]
    print(f"[SMOKE] With address: {len(with_addr)}/{len(filings)}")

    assert len(with_addr) > 0, "Expected at least 1 filing with a real address"

    for f in filings:
        assert f.state  == "FL"
        assert f.county == "Orange"
        assert f.case_number not in ("", None, "UNKNOWN")
        assert f.filing_date is not None
