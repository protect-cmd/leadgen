from __future__ import annotations

"""
Unit tests for scrapers/florida/sarasota.py (ClerkNet civil search).

Mocked tests (no network):
  - test_default_lookback_is_2_days / last_error_none_on_init
  - test_row_to_filing_keeps_only_evictions
  - test_row_to_filing_extracts_fields
  - test_clean_party_strips_tags_and_aliases
  - test_normalize_case_number
  - test_row_to_filing_rejects_non_case_rows

Live smoke test (requires SARASOTA_SMOKE=1 + US IP):
  - test_live_smoke_returns_filings
"""

import os
from datetime import date

import pytest

from scrapers.florida.sarasota import SarasotaScraper


# Real grid rows captured live (Case Number, Status, Primary, Secondary, File Date, Case Type).
_EVICTION_ROW = [
    "2026 CC 005586 NC", "OPEN-ACTIVE",
    "SUN OUTDOORS SARASOTA (Plaintiff - Alias) SNF PROPERTY LLC (Plaintiff)",
    "BASHAYEV, ALEXEY (Defendant)", "6/24/2026", "Evictions Residential Non-Monetary",
]
_NON_EVICTION_ROW = [
    "2026 CC 005564 NC", "OPEN-ACTIVE", "CAPITAL ONE NA (Plaintiff)",
    "WAHL, KATELYN R (Defendant)", "6/24/2026", "County Civil - $8,001 to $15,000",
]
_CIRCUIT_EVICTION_ROW = [
    "2026 CA 003436 SC", "OPEN-ACTIVE", "NGUYEN, KELLY T (Plaintiff)",
    "BECKER, ALICIA (Defendant) PREFFERED SETTLEMENT SERVICES INC (Defendant)",
    "6/24/2026", "EVICTION - CIRCUIT (SOUTH COUNTY)",
]


def test_default_lookback_is_2_days():
    assert SarasotaScraper().lookback_days == 2


def test_last_error_none_on_init():
    assert SarasotaScraper().last_error is None


def test_row_to_filing_keeps_only_evictions():
    assert SarasotaScraper._row_to_filing(_NON_EVICTION_ROW) is None
    assert SarasotaScraper._row_to_filing(_EVICTION_ROW) is not None
    assert SarasotaScraper._row_to_filing(_CIRCUIT_EVICTION_ROW) is not None


def test_row_to_filing_extracts_fields():
    f = SarasotaScraper._row_to_filing(_EVICTION_ROW)
    assert f.case_number == "2026-CC-005586-NC"
    # first plaintiff, alias + tag stripped
    assert f.landlord_name == "SUN OUTDOORS SARASOTA"
    assert f.tenant_name in ("Bashayev, Alexey", "BASHAYEV, ALEXEY")
    assert f.filing_date == date(2026, 6, 24)
    assert f.county == "Sarasota"
    assert f.state == "FL"
    assert f.property_address == "Unknown"


def test_circuit_eviction_defendant_is_first_party():
    f = SarasotaScraper._row_to_filing(_CIRCUIT_EVICTION_ROW)
    # first defendant only, alias dropped
    assert f.tenant_name in ("Becker, Alicia", "BECKER, ALICIA")
    assert "PREFFERED" not in f.tenant_name.upper()


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("CAPITAL ONE NA (Plaintiff)", "CAPITAL ONE NA"),
        ("BASHAYEV, ALEXEY (Defendant)", "BASHAYEV, ALEXEY"),
        ("SUN OUTDOORS SARASOTA (Plaintiff - Alias) SNF PROPERTY LLC (Plaintiff)", "SUN OUTDOORS SARASOTA"),
        ("", ""),
    ],
)
def test_clean_party_strips_tags_and_aliases(raw, expected):
    assert SarasotaScraper._clean_party(raw) == expected


def test_normalize_case_number():
    assert SarasotaScraper._normalize_case_number("2026 CC 005586 NC") == "2026-CC-005586-NC"


def test_row_to_filing_rejects_non_case_rows():
    assert SarasotaScraper._row_to_filing(["header", "row", "not", "a", "case", "x"]) is None
    assert SarasotaScraper._row_to_filing(["too", "few"]) is None


# ------------------------------------------------------------------ #
#  Live smoke test                                                    #
# ------------------------------------------------------------------ #

SMOKE = os.getenv("SARASOTA_SMOKE", "0") == "1"


@pytest.mark.skipif(not SMOKE, reason="Set SARASOTA_SMOKE=1 to run live")
def test_live_smoke_returns_filings():
    """
    Live test: hits the real ClerkNet portal.

    Run with:
        SARASOTA_SMOKE=1 python -m pytest tests/test_sarasota_scraper.py::test_live_smoke_returns_filings -v -s
    """
    import asyncio

    scraper = SarasotaScraper(lookback_days=4, headless=True)
    filings = asyncio.run(scraper.scrape())

    print(f"\n[SMOKE] last_error: {scraper.last_error}")
    print(f"[SMOKE] Total eviction filings: {len(filings)}")
    for f in filings[:10]:
        print(f"  {f.case_number} | {f.tenant_name} | landlord={f.landlord_name} | {f.filing_date}")

    assert scraper.last_error is None, f"scrape failed: {scraper.last_error}"
    assert len(filings) > 0, "Expected at least 1 eviction in a 4-day window"
    for f in filings:
        assert f.state == "FL"
        assert f.county == "Sarasota"
        assert f.filing_date is not None
