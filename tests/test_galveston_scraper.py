"""Tests for Galveston TX JP eviction scraper."""

from scrapers.texas.galveston import (
    GalvestonTXJPScraper,
    JP_JUDGES,
    JP_COURTS,
    EVICTION_KEYWORDS,
    STATE,
    COUNTY,
    NOTICE_TYPE,
)


def test_module_constants():
    """Confirm critical constants are correctly defined."""
    assert STATE == "TX"
    assert COUNTY == "Galveston"
    assert NOTICE_TYPE == "Eviction"
    assert len(JP_JUDGES) == 4
    precincts = {j["precinct"] for j in JP_JUDGES}
    assert precincts == {"JP1", "JP2", "JP3", "JP4"}
    assert JP_COURTS == {"JP1", "JP2", "JP3", "JP4"}
    assert "eviction" in EVICTION_KEYWORDS
    assert "forcible entry" in EVICTION_KEYWORDS


def test_scraper_instantiates():
    """Confirm scraper class can be instantiated."""
    scraper = GalvestonTXJPScraper()
    assert scraper.last_error is None


def test_filter_jp_evictions_keeps_jp_eviction_rows():
    """JP court + eviction case type should be kept."""
    scraper = GalvestonTXJPScraper()
    rows = [
        {
            "Case Number": "26-EV02-0382",
            "Court": "JP2",
            "Case Type": "Eviction",
            "Style": "Landlord LLC vs Tenant Smith",
        }
    ]
    result = scraper.filter_jp_evictions(rows)
    assert len(result) == 1
    assert result[0]["Case Number"] == "26-EV02-0382"


def test_filter_jp_evictions_drops_non_jp_courts():
    """District / County courts should be filtered out even if eviction-labeled."""
    scraper = GalvestonTXJPScraper()
    rows = [
        {"Case Number": "26-CV-12345", "Court": "District Court 56", "Case Type": "Eviction"},
        {"Case Number": "26-CC-99999", "Court": "County Court at Law 1", "Case Type": "Eviction"},
    ]
    result = scraper.filter_jp_evictions(rows)
    assert result == []


def test_filter_jp_evictions_drops_non_eviction_civil_cases():
    """Non-eviction civil cases in JP courts should be filtered out."""
    scraper = GalvestonTXJPScraper()
    rows = [
        {"Case Number": "26-SC-00100", "Court": "JP1", "Case Type": "Small Claims"},
        {"Case Number": "26-DC-00500", "Court": "JP2", "Case Type": "Debt Claim"},
    ]
    result = scraper.filter_jp_evictions(rows)
    assert result == []


def test_filter_jp_evictions_matches_case_number_pattern():
    """Even with missing case type, EV0N prefix in case number should match."""
    scraper = GalvestonTXJPScraper()
    rows = [
        {"Case Number": "26-EV03-0001", "Court": "JP3", "Case Type": ""},
    ]
    result = scraper.filter_jp_evictions(rows)
    assert len(result) == 1


def test_filter_jp_evictions_handles_forcible_entry_label():
    """Cases labeled with formal 'Forcible Entry and Detainer' should match."""
    scraper = GalvestonTXJPScraper()
    rows = [
        {
            "Case Number": "26-EV04-0050",
            "Court": "JP4",
            "Case Type": "Forcible Entry and Detainer",
        },
    ]
    result = scraper.filter_jp_evictions(rows)
    assert len(result) == 1


def test_filter_jp_evictions_handles_flexible_column_names():
    """Column header variation (Location vs Court, Type vs Case Type) should still work."""
    scraper = GalvestonTXJPScraper()
    rows = [
        {"Case #": "26-EV01-0123", "Location": "JP1", "Type": "Eviction"},
    ]
    result = scraper.filter_jp_evictions(rows)
    assert len(result) == 1


def test_filter_jp_evictions_empty_input():
    """Empty input returns empty list."""
    scraper = GalvestonTXJPScraper()
    assert scraper.filter_jp_evictions([]) == []