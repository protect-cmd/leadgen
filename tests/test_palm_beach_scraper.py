from __future__ import annotations

"""
Unit tests for the Palm Beach County FL scraper (eCaseView portal).

All tests are fixture-backed — no live network calls are made.
Playwright is never launched; the _launch_browser / _guest_login / _run_search
hooks are monkeypatched so scrape() can be exercised without a real browser.
"""

from datetime import date

import pytest

from models.filing import Filing
from scrapers.florida.palm_beach import PalmBeachScraper


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_filing(county: str = "Palm Beach") -> Filing:
    return Filing(
        case_number="2026-CC-001234",
        tenant_name="Jane Tenant",
        property_address="123 Main St, West Palm Beach, FL 33401",
        landlord_name="Acme Landlord LLC",
        filing_date=date(2026, 5, 9),
        court_date=None,
        state="FL",
        county=county,
        notice_type="Residential Eviction",
        source_url="https://appsgp.mypalmbeachclerk.com/ecaseview/",
    )


class _FakePage:
    """Minimal Playwright Page stub."""

    def __init__(self, *, title: str = "Portal", content: str = "<html></html>"):
        self._title = title
        self._content = content

    async def goto(self, url, **kwargs):
        pass

    async def wait_for_timeout(self, ms):
        pass

    async def wait_for_load_state(self, state, **kwargs):
        pass

    async def wait_for_selector(self, selector, **kwargs):
        pass

    async def title(self):
        return self._title

    async def content(self):
        return self._content

    async def query_selector(self, selector):
        return None

    async def query_selector_all(self, selector):
        return []

    async def evaluate(self, script, *args):
        return {"_error": "not implemented in stub"}


# ---------------------------------------------------------------------------
# PalmBeachScraper tests
# ---------------------------------------------------------------------------

class TestPalmBeachScraper:

    @pytest.mark.asyncio
    async def test_returns_filings_with_correct_state_and_county(self, monkeypatch):
        """Scraper returns Filing objects with state=FL and county=Palm Beach."""
        scraper = PalmBeachScraper(lookback_days=2)
        expected = [_make_filing()]

        async def fake_launch():
            return _FakePage()

        async def fake_close():
            pass

        async def fake_login(page):
            return True

        async def fake_search(page, start, today, **kwargs):
            return expected

        monkeypatch.setattr(scraper, "_launch_browser", fake_launch)
        monkeypatch.setattr(scraper, "_close_browser", fake_close)
        monkeypatch.setattr(scraper, "_guest_login", fake_login)
        monkeypatch.setattr(scraper, "_run_search", fake_search)

        filings = await scraper.scrape()

        assert len(filings) == 1
        assert filings[0].state == "FL"
        assert filings[0].county == "Palm Beach"
        assert filings[0].notice_type == "Residential Eviction"

    @pytest.mark.asyncio
    async def test_returns_empty_on_portal_load_failure(self, monkeypatch):
        """Scraper returns [] when the portal fails to load."""
        scraper = PalmBeachScraper(lookback_days=2)

        class _FailPage(_FakePage):
            async def goto(self, url, **kwargs):
                raise ConnectionError("portal unreachable")

        async def fake_launch():
            return _FailPage()

        async def fake_close():
            pass

        monkeypatch.setattr(scraper, "_launch_browser", fake_launch)
        monkeypatch.setattr(scraper, "_close_browser", fake_close)

        filings = await scraper.scrape()
        assert filings == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_results(self, monkeypatch):
        """Scraper returns [] when search yields no rows."""
        scraper = PalmBeachScraper(lookback_days=2)

        async def fake_launch():
            return _FakePage()

        async def fake_close():
            pass

        async def fake_login(page):
            return True

        async def fake_search(page, start, today, **kwargs):
            return []

        monkeypatch.setattr(scraper, "_launch_browser", fake_launch)
        monkeypatch.setattr(scraper, "_close_browser", fake_close)
        monkeypatch.setattr(scraper, "_guest_login", fake_login)
        monkeypatch.setattr(scraper, "_run_search", fake_search)

        filings = await scraper.scrape()
        assert filings == []

    def test_grid_row_to_filing_valid_row(self):
        """_grid_row_to_filing produces a Filing from a results-grid row."""
        scraper = PalmBeachScraper(lookback_days=2)
        row = {
            "case_number": "2026-CC-001234",
            "case_style": "Acme Properties LLC VS Smith, John",
            "filed": "05/08/2026",
            "case_type": "Landlord Tenant Eviction",
        }
        filing = scraper._grid_row_to_filing(row, date(2026, 5, 9))
        assert filing is not None
        assert filing.case_number == "2026-CC-001234"
        assert filing.landlord_name == "Acme Properties LLC"
        assert "Smith" in filing.tenant_name
        assert filing.filing_date == date(2026, 5, 8)
        assert filing.state == "FL"
        assert filing.county == "Palm Beach"

    def test_grid_row_to_filing_skips_empty_case_number(self):
        """_grid_row_to_filing returns None for a row with no case number."""
        scraper = PalmBeachScraper(lookback_days=2)
        filing = scraper._grid_row_to_filing({"case_number": "", "case_style": "X VS Y"}, date.today())
        assert filing is None

    def test_split_style(self):
        """_split_style correctly splits 'PLAINTIFF VS DEFENDANT'."""
        p, d = PalmBeachScraper._split_style("Green Realty LLC VS Johnson, Mary")
        assert p == "Green Realty LLC"
        assert d == "Johnson, Mary"

    def test_is_eviction_row_matches_cc_case(self):
        """_is_eviction_row returns True for a CC case number."""
        row = {"case_number": "2026CC001234", "case_type": "", "case_style": ""}
        assert PalmBeachScraper._is_eviction_row(row) is True

    def test_is_eviction_row_matches_type_label(self):
        """_is_eviction_row returns True when case_type contains EVICT."""
        row = {"case_number": "2026-001", "case_type": "Eviction", "case_style": ""}
        assert PalmBeachScraper._is_eviction_row(row) is True
