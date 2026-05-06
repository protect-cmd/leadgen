from __future__ import annotations

from scrapers.tennessee.davidson import DavidsonTNScraper


def test_davidson_scraper_records_last_error_when_docket_fetch_fails(monkeypatch):
    scraper = DavidsonTNScraper(lookback_days=2)

    def fail_fetch():
        raise ConnectionResetError("connection reset")

    monkeypatch.setattr(scraper, "_fetch_docket_list", fail_fetch)

    filings = scraper.scrape()

    assert filings == []
    assert scraper.last_error == "failed to fetch docket list: connection reset"
