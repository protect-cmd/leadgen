from __future__ import annotations

"""
Unit tests for scrapers/florida/duval.py (Duval County / Jacksonville CORE).

Mocked tests (no network):
  - test_default_lookback_is_2_days
  - test_last_error_none_on_init
  - test_parse_case_detail_real_sample
  - test_parse_parties_business_plaintiff
  - test_normalize_address_fixes_state_zip
  - test_parse_case_detail_no_parties / no_date
  - test_scrape_returns_empty_on_portal_failure
  - test_filing_schema_contract

Live smoke test (requires DUVAL_SMOKE=1 + US IP):
  - test_live_smoke_returns_filings
"""

import os
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from scrapers.florida.duval import DuvalScraper


# Captured live CORE case-detail innerText (case 2026-CC-012074), June 2026.
_REAL_DETAIL = (
    "Case 16-2026-CC-012074-AXXX-MA\n"
    "Department\tCounty Civil\tDivision\tCC-Q\n"
    "Case Status\tOPEN\tFile Date\t6/24/2026\n"
    "Judge Name\tHUDSON, DAWN\tOfficer\t\n"
    "Parties\n"
    "Name / DOB / DL / ID #\tParty Type\n"
    "Race / Sex\tAddress\n"
    "GARY C HURSTON SR\tPLAINTIFF\n"
    "/\t\n"
    "2081 CHAFEE ROAD SOUTH\n"
    "LOT 42\n"
    "JACKSONVILLE, FL32221\n"
    "\n"
    "JONATHAN WHALEY\tDEFENDANT\n"
    "/\t\n"
    "2081 CHAFFEE ROAD SOUTH\n"
    "LOT 42\n"
    "JACKSONVILLE, FL32221\n"
    "\n"
    "ANGEL WHALEY\tDEFENDANT\n"
    "/\t\n"
    "2081 CHAFFEE ROAD SOUTH\n"
    "LOT 42\n"
    "JACKSONVILLE, FL32221\n"
    "Attorneys\n"
    "Attorney\tAddress\tFor Parties\n"
    "Fees\n"
)


# ------------------------------------------------------------------ #
#  Init / config                                                      #
# ------------------------------------------------------------------ #

def test_default_lookback_is_2_days():
    assert DuvalScraper().lookback_days == 2


def test_last_error_none_on_init():
    assert DuvalScraper().last_error is None


def test_scraper_init_stores_lookback():
    assert DuvalScraper(lookback_days=9).lookback_days == 9


# ------------------------------------------------------------------ #
#  Case-detail parsing                                                #
# ------------------------------------------------------------------ #

def test_parse_case_detail_real_sample():
    parsed = DuvalScraper._parse_case_detail(_REAL_DETAIL)
    assert parsed["file_date"] == date(2026, 6, 24)
    assert parsed["landlord"] == "GARY C HURSTON SR"
    # first defendant becomes the tenant
    assert parsed["tenant"] == "JONATHAN WHALEY"
    # defendant address = the property, with FL32221 normalized to "FL 32221"
    assert parsed["address"] == "2081 CHAFFEE ROAD SOUTH, LOT 42, JACKSONVILLE, FL 32221"


def test_parse_parties_separates_plaintiff_and_defendants():
    parties = DuvalScraper._parse_parties(_REAL_DETAIL)
    types = [p["type"] for p in parties]
    assert types.count("PLAINTIFF") == 1
    assert types.count("DEFENDANT") == 2
    assert parties[0]["name"] == "GARY C HURSTON SR"


def test_parse_case_detail_no_parties_section():
    parsed = DuvalScraper._parse_case_detail("Case 123\nFile Date\t6/24/2026\n")
    assert parsed["file_date"] == date(2026, 6, 24)
    assert parsed["landlord"] is None
    assert parsed["tenant"] is None
    assert parsed["address"] is None


def test_parse_case_detail_no_date():
    parsed = DuvalScraper._parse_case_detail("Parties\nJOHN DOE\tDEFENDANT\n123 MAIN ST\n")
    assert parsed["file_date"] is None
    assert parsed["tenant"] == "JOHN DOE"


@pytest.mark.parametrize(
    "lines, expected",
    [
        (["742 EVERGREEN TER", "JACKSONVILLE, FL32205"], "742 EVERGREEN TER, JACKSONVILLE, FL 32205"),
        (["100 W BAY ST", "APT 5", "JACKSONVILLE, FL 32202-1234"], "100 W BAY ST, APT 5, JACKSONVILLE, FL 32202-1234"),
        ([], None),
        (["   "], None),
    ],
)
def test_normalize_address_fixes_state_zip(lines, expected):
    assert DuvalScraper._normalize_address(lines) == expected


# ------------------------------------------------------------------ #
#  Failure handling                                                   #
# ------------------------------------------------------------------ #

def test_scrape_returns_empty_on_portal_failure():
    """If the portal never loads, scrape sets last_error and returns []."""
    import asyncio

    scraper = DuvalScraper.__new__(DuvalScraper)
    scraper.lookback_days = 2
    scraper.headless = True
    scraper.max_pages = 15
    scraper.last_error = None

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock(side_effect=Exception("portal down"))

    with patch.object(scraper, "_launch_browser", return_value=mock_page), \
         patch.object(scraper, "_close_browser", new_callable=AsyncMock):
        result = asyncio.run(scraper.scrape())

    assert result == []
    assert scraper.last_error == "portal down"


# ------------------------------------------------------------------ #
#  Schema contract                                                    #
# ------------------------------------------------------------------ #

def test_filing_schema_contract():
    from models.filing import Filing

    f = Filing(
        case_number      = "2026-CC-012074-AXXX",
        tenant_name      = "Whaley, Jonathan",
        property_address = "2081 CHAFFEE ROAD SOUTH, LOT 42, JACKSONVILLE, FL 32221",
        landlord_name    = "Gary C Hurston Sr",
        filing_date      = date(2026, 6, 24),
        court_date       = None,
        state            = "FL",
        county           = "Duval",
        notice_type      = "Residential Eviction",
        source_url       = "https://core.duvalclerk.com/CoreCms.aspx?mode=PublicAccess",
    )
    assert f.county == "Duval"
    assert f.state == "FL"


# ------------------------------------------------------------------ #
#  Live smoke test                                                    #
# ------------------------------------------------------------------ #

SMOKE = os.getenv("DUVAL_SMOKE", "0") == "1"


@pytest.mark.skipif(not SMOKE, reason="Set DUVAL_SMOKE=1 to run live")
def test_live_smoke_returns_filings():
    """
    Live test: hits the real CORE portal.

    Run with:
        DUVAL_SMOKE=1 python -m pytest tests/test_duval_scraper.py::test_live_smoke_returns_filings -v -s
    """
    import asyncio

    scraper = DuvalScraper(lookback_days=3, headless=True)
    filings = asyncio.run(scraper.scrape())

    print(f"\n[SMOKE] last_error: {scraper.last_error}")
    print(f"[SMOKE] Total filings: {len(filings)}")
    for f in filings[:10]:
        print(f"  {f.case_number} | {f.tenant_name} | {f.property_address} | {f.filing_date}")

    assert scraper.last_error is None, f"scrape failed: {scraper.last_error}"
    assert len(filings) > 0, "Expected at least 1 eviction filing in a 3-day window"

    with_addr = [f for f in filings if f.property_address not in ("Unknown", "", None)]
    print(f"[SMOKE] With address: {len(with_addr)}/{len(filings)}")
    assert len(with_addr) > 0, "Expected at least 1 filing with a parsed address"

    for f in filings:
        assert f.state == "FL"
        assert f.county == "Duval"
        assert f.filing_date is not None
