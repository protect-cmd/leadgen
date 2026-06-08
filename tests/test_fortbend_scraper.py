"""Tests for Fort Bend TX JP eviction scraper."""

from scrapers.texas.fortbend import (
    FortBendTXJPScraper,
    STATE,
    COUNTY,
    NOTICE_TYPE,
)


def test_module_constants():
    """Confirm critical constants are correctly defined."""
    assert STATE == "TX"
    assert COUNTY == "Fort Bend"
    assert NOTICE_TYPE == "Eviction"


def test_scraper_instantiates():
    """Confirm scraper class can be instantiated."""
    scraper = FortBendTXJPScraper()
    assert scraper.last_error is None