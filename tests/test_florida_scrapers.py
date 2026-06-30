from __future__ import annotations

"""
Unit tests for the Florida scrapers (Miami-Dade, Broward, Hillsborough).

All tests are fixture-backed — no live network calls are made.
Playwright is never launched; the _launch_browser hook is monkeypatched
so the async scrape() methods can be exercised without a real browser.
"""

import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.filing import Filing
from scrapers.florida.broward import BrowardScraper
from scrapers.florida.hillsborough import HillsboroughScraper
from scrapers.florida.miami_dade import MiamiDadeScraper
from scrapers.florida.palm_beach import PalmBeachScraper
from scripts.smoke_scrapers import parse_states


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_filing(county: str) -> Filing:
    return Filing(
        case_number="2026-CC-001234",
        tenant_name="Jane Tenant",
        property_address="123 Main St, Miami, FL 33101",
        landlord_name="Acme Landlord LLC",
        filing_date=date(2026, 5, 9),
        court_date=None,
        state="FL",
        county=county,
        notice_type="Residential Eviction",
        source_url="https://example.com",
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
# Miami-Dade tests
# ---------------------------------------------------------------------------

class TestMiamiDadeScraper:

    @pytest.mark.asyncio
    async def test_returns_filings_with_correct_state_and_county(self, monkeypatch):
        """Scraper returns Filing objects with state=FL and county=Miami-Dade."""
        scraper = MiamiDadeScraper(lookback_days=2)

        expected = [_make_filing("Miami-Dade")]

        async def fake_launch():
            return _FakePage()

        async def fake_close():
            pass

        async def fake_search(page, start, today, vs, vsg, ev):
            return expected

        monkeypatch.setattr(scraper, "_launch_browser", fake_launch)
        monkeypatch.setattr(scraper, "_close_browser", fake_close)
        monkeypatch.setattr(scraper, "_search_by_date", fake_search)

        # Provide a non-empty viewstate so the search path is taken
        async def fake_get_input(page, sel):
            return "fake-viewstate-value"

        monkeypatch.setattr(MiamiDadeScraper, "_get_input_value", staticmethod(fake_get_input))

        filings = await scraper.scrape()

        assert len(filings) == 1
        assert filings[0].state == "FL"
        assert filings[0].county == "Miami-Dade"
        assert filings[0].notice_type == "Residential Eviction"

    @pytest.mark.asyncio
    async def test_returns_empty_on_portal_load_failure(self, monkeypatch):
        """Scraper returns [] when the portal page fails to load."""
        scraper = MiamiDadeScraper(lookback_days=2)

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
    async def test_returns_empty_on_search_http_error(self, monkeypatch):
        """Scraper returns [] when the search API returns an error."""
        scraper = MiamiDadeScraper(lookback_days=2)

        async def fake_launch():
            return _FakePage()

        async def fake_close():
            pass

        async def fake_get_input(page, sel):
            return "fake-viewstate"

        async def fake_search(page, start, today, vs, vsg, ev):
            return []

        monkeypatch.setattr(scraper, "_launch_browser", fake_launch)
        monkeypatch.setattr(scraper, "_close_browser", fake_close)
        monkeypatch.setattr(MiamiDadeScraper, "_get_input_value", staticmethod(fake_get_input))
        monkeypatch.setattr(scraper, "_search_by_date", fake_search)

        filings = await scraper.scrape()
        assert filings == []

    @pytest.mark.asyncio
    async def test_parse_html_results_empty_table(self):
        """_parse_html_results returns [] for a table with no data rows."""
        scraper = MiamiDadeScraper(lookback_days=2)
        html = "<html><body><table><thead><tr><th>Case Number</th></tr></thead><tbody></tbody></table></body></html>"
        result = scraper._parse_html_results(html, date.today())
        assert result == []

    def test_parse_html_results_with_data_row(self):
        """_parse_html_results extracts a Filing from a valid table row."""
        scraper = MiamiDadeScraper(lookback_days=2)
        html = (
            "<html><body><table><tbody>"
            "<tr>"
            "<td>2026-CC-001</td>"
            "<td>05/07/2026</td>"
            "<td>RE</td>"
            "<td>Acme LLC</td>"
            "<td>John Doe</td>"
            "<td>100 Oak Ave, Miami FL 33101</td>"
            "</tr>"
            "</tbody></table></body></html>"
        )
        result = scraper._parse_html_results(html, date(2026, 5, 9))
        assert len(result) == 1
        f = result[0]
        assert f.case_number == "2026-CC-001"
        assert f.state == "FL"
        assert f.county == "Miami-Dade"
        assert f.filing_date == date(2026, 5, 7)
        assert f.landlord_name == "Acme LLC"
        assert f.tenant_name == "John Doe"


# ---------------------------------------------------------------------------
# Broward tests
# ---------------------------------------------------------------------------

class TestBrowardScraper:

    @pytest.mark.asyncio
    async def test_returns_filings_with_correct_state_and_county(self, monkeypatch):
        """Scraper returns Filing objects with state=FL and county=Broward."""
        scraper = BrowardScraper(lookback_days=2)
        expected = [_make_filing("Broward")]

        async def fake_launch():
            return _FakePage()

        async def fake_close():
            pass

        async def fake_ui(page, start, today):
            return expected

        monkeypatch.setattr(scraper, "_launch_browser", fake_launch)
        monkeypatch.setattr(scraper, "_close_browser", fake_close)
        monkeypatch.setattr(scraper, "_search_via_ui", fake_ui)

        filings = await scraper.scrape()

        assert len(filings) == 1
        assert filings[0].state == "FL"
        assert filings[0].county == "Broward"
        assert filings[0].notice_type == "Residential Eviction"

    @pytest.mark.asyncio
    async def test_returns_empty_on_portal_load_failure(self, monkeypatch):
        """Scraper returns [] when the portal fails to load."""
        scraper = BrowardScraper(lookback_days=2)

        class _FailPage(_FakePage):
            async def goto(self, url, **kwargs):
                raise ConnectionError("network failure")

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
        scraper = BrowardScraper(lookback_days=2)

        async def fake_launch():
            return _FakePage()

        async def fake_close():
            pass

        async def fake_ui(page, start, today):
            return []

        monkeypatch.setattr(scraper, "_launch_browser", fake_launch)
        monkeypatch.setattr(scraper, "_close_browser", fake_close)
        monkeypatch.setattr(scraper, "_search_via_ui", fake_ui)

        filings = await scraper.scrape()
        assert filings == []

    def test_cells_to_filing_valid_row(self):
        """_cells_to_filing produces a Filing from a well-formed row."""
        scraper = BrowardScraper(lookback_days=2)
        cells = ["2026-CC-555", "05/07/2026", "Big Landlord Inc", "Bob Tenant", "200 Pine St"]
        filing = scraper._cells_to_filing(cells, date(2026, 5, 9))
        assert filing is not None
        assert filing.case_number == "2026-CC-555"
        assert filing.state == "FL"
        assert filing.county == "Broward"

    def test_cells_to_filing_skips_header_row(self):
        """_cells_to_filing returns None for a header row."""
        scraper = BrowardScraper(lookback_days=2)
        filing = scraper._cells_to_filing(["Case Number", "Filing Date"], date.today())
        assert filing is None


# ---------------------------------------------------------------------------
# Hillsborough tests
# ---------------------------------------------------------------------------

class TestHillsboroughScraper:

    @pytest.mark.asyncio
    async def test_returns_filings_with_correct_state_and_county(self, monkeypatch):
        """Scraper returns Filing objects with state=FL and county=Hillsborough."""
        scraper = HillsboroughScraper(lookback_days=2)
        expected = [_make_filing("Hillsborough")]

        async def fake_launch():
            return _FakePage()

        async def fake_close():
            pass

        async def fake_search(page, start, today):
            return expected

        monkeypatch.setattr(scraper, "_launch_browser", fake_launch)
        monkeypatch.setattr(scraper, "_close_browser", fake_close)
        monkeypatch.setattr(scraper, "_run_search", fake_search)

        filings = await scraper.scrape()

        assert len(filings) == 1
        assert filings[0].state == "FL"
        assert filings[0].county == "Hillsborough"
        assert filings[0].notice_type == "Residential Eviction"

    @pytest.mark.asyncio
    async def test_returns_empty_on_portal_load_failure(self, monkeypatch):
        """Scraper returns [] when the HOVER portal fails to load."""
        scraper = HillsboroughScraper(lookback_days=2)

        class _FailPage(_FakePage):
            async def goto(self, url, **kwargs):
                raise ConnectionError("403 Forbidden")

        async def fake_launch():
            return _FailPage()

        async def fake_close():
            pass

        monkeypatch.setattr(scraper, "_launch_browser", fake_launch)
        monkeypatch.setattr(scraper, "_close_browser", fake_close)

        filings = await scraper.scrape()
        assert filings == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_403_in_content(self, monkeypatch):
        """Scraper returns [] when portal content signals a 403 block."""
        scraper = HillsboroughScraper(lookback_days=2)

        async def fake_launch():
            return _FakePage(content="<html>403 Forbidden — Access Denied</html>")

        async def fake_close():
            pass

        monkeypatch.setattr(scraper, "_launch_browser", fake_launch)
        monkeypatch.setattr(scraper, "_close_browser", fake_close)

        filings = await scraper.scrape()
        assert filings == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_results(self, monkeypatch):
        """Scraper returns [] when search yields no rows."""
        scraper = HillsboroughScraper(lookback_days=2)

        async def fake_launch():
            return _FakePage()

        async def fake_close():
            pass

        async def fake_search(page, start, today):
            return []

        monkeypatch.setattr(scraper, "_launch_browser", fake_launch)
        monkeypatch.setattr(scraper, "_close_browser", fake_close)
        monkeypatch.setattr(scraper, "_run_search", fake_search)

        filings = await scraper.scrape()
        assert filings == []

    def test_grid_row_to_filing_valid_row(self):
        """_grid_row_to_filing produces a Filing from a results-grid row."""
        scraper = HillsboroughScraper(lookback_days=2)
        row = {
            "case_number": "2026-CC-777",
            "case_style": "Tampa Realty LLC VS Renter, Sue",
            "filed": "05/08/2026",
            "case_type": "LT Residential Eviction",
        }
        filing = scraper._grid_row_to_filing(row, date(2026, 5, 9))
        assert filing is not None
        assert filing.case_number == "2026-CC-777"
        assert filing.landlord_name == "Tampa Realty LLC"
        assert "Renter" in filing.tenant_name
        assert filing.filing_date == date(2026, 5, 8)
        assert filing.state == "FL"
        assert filing.county == "Hillsborough"


# ---------------------------------------------------------------------------
# Palm Beach tests
# ---------------------------------------------------------------------------

class TestPalmBeachScraper:

    @pytest.mark.asyncio
    async def test_returns_filings_with_correct_state_and_county(self, monkeypatch):
        """Scraper returns Filing objects with state=FL and county=Palm Beach."""
        scraper = PalmBeachScraper(lookback_days=2)
        expected = [_make_filing("Palm Beach")]

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

        async def fake_search(page, start, today):
            return []

        monkeypatch.setattr(scraper, "_launch_browser", fake_launch)
        monkeypatch.setattr(scraper, "_close_browser", fake_close)
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


# ---------------------------------------------------------------------------
# parse_states / STATE_ALIASES tests
# ---------------------------------------------------------------------------

class TestParseStates:

    def test_florida_resolves(self):
        assert parse_states("florida") == ["florida"]

    def test_fl_alias_resolves(self):
        assert parse_states("fl") == ["florida"]

    def test_miami_alias_resolves(self):
        assert parse_states("miami") == ["florida"]

    def test_miami_dade_alias_resolves(self):
        assert parse_states("miami-dade") == ["florida"]

    def test_broward_alias_resolves(self):
        assert parse_states("broward") == ["florida"]

    def test_hillsborough_alias_resolves(self):
        assert parse_states("hillsborough") == ["florida"]

    def test_florida_included_in_all(self):
        states = parse_states("all")
        assert "florida" in states

    def test_palm_beach_alias_resolves(self):
        assert parse_states("palm_beach") == ["palm_beach"]

    def test_palm_beach_hyphen_alias_resolves(self):
        assert parse_states("palm-beach") == ["palm_beach"]

    def test_mixed_states(self):
        states = parse_states("fl,tn")
        assert states == ["florida", "tennessee"]
