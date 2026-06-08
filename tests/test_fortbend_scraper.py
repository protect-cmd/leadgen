"""Tests for Fort Bend TX JP eviction scraper."""

from scrapers.texas.fortbend import (
    FortBendTXJPScraper,
    EVICTION_KEYWORDS,
    STATE,
    COUNTY,
    NOTICE_TYPE,
    PORTAL_BASE,
)


def test_module_constants():
    """Confirm critical constants are correctly defined."""
    assert STATE == "TX"
    assert COUNTY == "Fort Bend"
    assert NOTICE_TYPE == "Eviction"
    assert "eviction" in EVICTION_KEYWORDS
    assert "forcible entry" in EVICTION_KEYWORDS


def test_scraper_instantiates():
    """Confirm scraper class can be instantiated."""
    scraper = FortBendTXJPScraper()
    assert scraper.last_error is None


def test_filter_evictions_keeps_eviction_row():
    """Standard eviction row should be kept."""
    s = FortBendTXJPScraper()
    rows = [{"Case Number": "26-12345", "Case Type": "Eviction"}]
    assert len(s.filter_evictions(rows)) == 1


def test_filter_evictions_drops_non_eviction():
    """Non-eviction case types should be filtered out."""
    s = FortBendTXJPScraper()
    rows = [
        {"Case Number": "26-CV-100", "Case Type": "Debt Claim"},
        {"Case Number": "26-SC-200", "Case Type": "Small Claims"},
    ]
    assert s.filter_evictions(rows) == []


def test_filter_evictions_matches_forcible_entry_label():
    """Formal label 'Forcible Entry and Detainer' should match."""
    s = FortBendTXJPScraper()
    rows = [{"Case Number": "26-456", "Case Type": "Forcible Entry and Detainer"}]
    assert len(s.filter_evictions(rows)) == 1


def test_filter_evictions_handles_flexible_column_names():
    """Variations like 'Type' or 'Cause of Action' should work."""
    s = FortBendTXJPScraper()
    rows = [{"Case Number": "26-789", "Type": "Eviction"}]
    assert len(s.filter_evictions(rows)) == 1
    rows = [{"Case Number": "26-321", "Cause of Action": "Eviction"}]
    assert len(s.filter_evictions(rows)) == 1


def test_filter_evictions_empty_input():
    """Empty input returns empty list."""
    s = FortBendTXJPScraper()
    assert s.filter_evictions([]) == []


def test_normalize_url_absolute():
    """Absolute URLs pass through unchanged."""
    s = FortBendTXJPScraper()
    abs_url = "https://example.com/foo"
    assert s._normalize_url(abs_url) == abs_url


def test_normalize_url_root_path():
    """Root-relative paths prepend PORTAL_BASE."""
    s = FortBendTXJPScraper()
    result = s._normalize_url("/PublicAccess/CaseDetail.aspx?CaseID=12345")
    assert result == PORTAL_BASE + "/PublicAccess/CaseDetail.aspx?CaseID=12345"


def test_normalize_url_relative():
    """Bare relative paths get the PublicAccess base."""
    s = FortBendTXJPScraper()
    result = s._normalize_url("CaseDetail.aspx?CaseID=12345")
    assert result == f"{PORTAL_BASE}/PublicAccess/CaseDetail.aspx?CaseID=12345"


def test_normalize_url_empty():
    """Empty href returns empty string."""
    s = FortBendTXJPScraper()
    assert s._normalize_url("") == ""
    assert s._normalize_url(None) == ""