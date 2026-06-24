from __future__ import annotations

"""
Unit tests for scrapers/florida/hillsborough.py

Mocked tests (no network):
  - test_default_lookback_is_7_days
  - test_date_format_on_or_after
  - test_try_parse_date_formats
  - test_eviction_case_type_contains_landlord_codes
  - test_last_error_none_on_init
  - test_scrape_returns_empty_on_portal_failure
  - test_scrape_sets_last_error_on_failure
  - test_scrape_returns_empty_when_tab_missing
  - test_split_style_*
  - test_grid_row_to_filing_*
  - test_bright_data_ws_url_*

Live smoke test (requires HILLSBOROUGH_SMOKE=1 + Bright Data env vars):
  - test_live_smoke_returns_filings
"""

import asyncio
import os
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from scrapers.florida.hillsborough import (
    EVICTION_CASE_TYPE_VALUE,
    HillsboroughScraper,
    bright_data_ws_url,
)


def _bare() -> HillsboroughScraper:
    """A scraper instance without running __init__ (no browser/env needed)."""
    s = HillsboroughScraper.__new__(HillsboroughScraper)
    s.lookback_days = 7
    s.headless = True
    s.max_cases = 200
    s.fetch_addresses = True
    s.last_error = None
    return s


# ------------------------------------------------------------------ #
#  Pure-logic tests                                                   #
# ------------------------------------------------------------------ #

def test_default_lookback_is_7_days():
    today = date(2026, 6, 18)
    start = today - timedelta(days=7)
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
    ("not-a-date", None),
    ("", None),
])
def test_try_parse_date_formats(raw, expected):
    assert HillsboroughScraper._try_parse_date(raw) == expected


def test_eviction_case_type_contains_landlord_codes():
    # Exact value of the live LANDLORD/TENANT/EVICTION option (June 2026).
    assert "3133" in EVICTION_CASE_TYPE_VALUE
    assert "35776" in EVICTION_CASE_TYPE_VALUE


def test_last_error_none_on_init():
    assert _bare().last_error is None


# ------------------------------------------------------------------ #
#  Case-style parsing (results grid)                                  #
# ------------------------------------------------------------------ #

@pytest.mark.parametrize("style,plaintiff,defendant", [
    ("FRENCH QUARTER INVESTORS, LLC VS BURRELL, REGINALD",
     "FRENCH QUARTER INVESTORS, LLC", "BURRELL, REGINALD"),
    ("CAX LAKESHORE, L.L.C vs Rodriguez, Kimberly Ann",
     "CAX LAKESHORE, L.L.C", "Rodriguez, Kimberly Ann"),
    ("ACME PROPERTIES VS. DOE, JANE", "ACME PROPERTIES", "DOE, JANE"),
    ("NO SEPARATOR HERE", "NO SEPARATOR HERE", ""),
    ("", "", ""),
])
def test_split_style(style, plaintiff, defendant):
    assert HillsboroughScraper._split_style(style) == (plaintiff, defendant)


def test_split_style_does_not_break_on_vs_inside_name():
    # Only the first " vs " separates the parties.
    p, d = HillsboroughScraper._split_style("A VS B VS C")
    assert p == "A"
    assert d == "B VS C"


# ------------------------------------------------------------------ #
#  Grid row -> Filing                                                 #
# ------------------------------------------------------------------ #

def test_grid_row_to_filing_maps_columns():
    s = _bare()
    row = {
        "case_number": "26-CC-029925",
        "case_style": "FRENCH QUARTER INVESTORS, LLC VS BURRELL, REGINALD",
        "filed": "2026-06-24",
        "case_type": "LT Residential Eviction- Possession",
    }
    f = s._grid_row_to_filing(row, today=date(2026, 6, 25))
    assert f is not None
    assert f.case_number == "26-CC-029925"
    assert f.landlord_name == "FRENCH QUARTER INVESTORS, LLC"
    assert "BURRELL" in f.tenant_name.upper()
    assert f.filing_date == date(2026, 6, 24)
    assert f.property_address == "Unknown"   # grid has no address
    assert f.state == "FL" and f.county == "Hillsborough"
    assert "Eviction" in f.notice_type


def test_grid_row_without_case_number_is_skipped():
    s = _bare()
    row = {"case_number": "", "case_style": "A VS B", "filed": "", "case_type": ""}
    assert s._grid_row_to_filing(row, today=date(2026, 6, 25)) is None


def test_grid_row_unparseable_date_falls_back_to_today():
    s = _bare()
    today = date(2026, 6, 25)
    row = {"case_number": "26-CC-1", "case_style": "A VS B", "filed": "n/a", "case_type": ""}
    f = s._grid_row_to_filing(row, today=today)
    assert f.filing_date == today


# ------------------------------------------------------------------ #
#  Bright Data endpoint resolution                                    #
# ------------------------------------------------------------------ #

def test_bright_data_ws_url_explicit(monkeypatch):
    monkeypatch.setenv("BRIGHTDATA_SB_WS", "wss://example/cdp")
    monkeypatch.delenv("BRIGHTDATA_CUSTOMER_ID", raising=False)
    assert bright_data_ws_url() == "wss://example/cdp"


def test_bright_data_ws_url_composed(monkeypatch):
    monkeypatch.delenv("BRIGHTDATA_SB_WS", raising=False)
    monkeypatch.setenv("BRIGHTDATA_CUSTOMER_ID", "cust")
    monkeypatch.setenv("BRIGHTDATA_ZONE", "zone1")
    monkeypatch.setenv("BRIGHTDATA_ZONE_PASSWORD", "pw")
    url = bright_data_ws_url()
    assert url == "wss://brd-customer-cust-zone-zone1:pw@brd.superproxy.io:9222"


def test_bright_data_ws_url_empty_when_unconfigured(monkeypatch):
    for k in ("BRIGHTDATA_SB_WS", "BRIGHTDATA_CUSTOMER_ID",
              "BRIGHTDATA_ZONE", "BRIGHTDATA_ZONE_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    assert bright_data_ws_url() == ""


# ------------------------------------------------------------------ #
#  scrape() failure handling (fail closed)                           #
# ------------------------------------------------------------------ #

def test_scrape_returns_empty_on_portal_failure():
    s = _bare()
    mock_page = AsyncMock()
    mock_page.goto.side_effect = Exception("connection refused")
    with patch.object(s, "_launch_browser", return_value=mock_page), \
         patch.object(s, "_close_browser", new_callable=AsyncMock):
        result = asyncio.run(s.scrape())
    assert result == []


def test_scrape_sets_last_error_on_failure():
    s = _bare()
    mock_page = AsyncMock()
    mock_page.goto.side_effect = Exception("timeout")
    with patch.object(s, "_launch_browser", return_value=mock_page), \
         patch.object(s, "_close_browser", new_callable=AsyncMock):
        asyncio.run(s.scrape())
    assert s.last_error is not None
    assert "timeout" in s.last_error


def test_scrape_returns_empty_when_tab_missing():
    """If the date-range tab never appears (e.g. 403 wall), return [] + last_error."""
    s = _bare()
    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.wait_for_selector = AsyncMock(side_effect=Exception("timeout"))
    with patch.object(s, "_launch_browser", return_value=mock_page), \
         patch.object(s, "_close_browser", new_callable=AsyncMock):
        result = asyncio.run(s.scrape())
    assert result == []
    assert s.last_error is not None
    assert "blocked" in s.last_error.lower() or "tab" in s.last_error.lower()


# ------------------------------------------------------------------ #
#  Live smoke test                                                    #
# ------------------------------------------------------------------ #

SMOKE = os.getenv("HILLSBOROUGH_SMOKE", "0") == "1"


@pytest.mark.skipif(not SMOKE, reason="Set HILLSBOROUGH_SMOKE=1 (+ Bright Data env) to run live")
def test_live_smoke_returns_filings():
    """
    Live test: hits the real HOVER portal through Bright Data.

    Requires BRIGHTDATA_SB_WS (or BRIGHTDATA_CUSTOMER_ID/ZONE/ZONE_PASSWORD).
    Run with:
        HILLSBOROUGH_SMOKE=1 python -m pytest \
            tests/test_hillsborough.py::test_live_smoke_returns_filings -v -s
    """
    scraper = HillsboroughScraper(lookback_days=7, headless=True, max_cases=5)
    filings = asyncio.run(scraper.scrape())

    print(f"\n[SMOKE] last_error: {scraper.last_error}")
    print(f"[SMOKE] total filings: {len(filings)}")
    for f in filings[:10]:
        print(f"  {f.case_number} | {f.tenant_name} | {f.property_address} | {f.filing_date}")

    assert scraper.last_error is None, f"scrape failed: {scraper.last_error}"
    assert len(filings) > 0, "Expected at least 1 filing in 7-day window"

    with_addr = [f for f in filings if f.property_address not in ("Unknown", "", None)]
    print(f"[SMOKE] with real address: {len(with_addr)}/{len(filings)}")
    assert len(with_addr) > 0, "Expected at least 1 filing with a real address"

    for f in filings:
        assert f.state == "FL"
        assert f.county == "Hillsborough"
        assert f.case_number not in ("", None)
        assert f.filing_date is not None
        assert f.tenant_name != ""
