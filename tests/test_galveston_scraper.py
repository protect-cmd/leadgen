"""Tests for Galveston TX JP eviction scraper."""

from scrapers.texas.galveston import (
    GalvestonTXJPScraper,
    JP_JUDGES,
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


def test_scraper_instantiates():
    """Confirm scraper class can be instantiated."""
    scraper = GalvestonTXJPScraper()
    assert scraper.last_error is None