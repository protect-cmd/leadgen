from __future__ import annotations

import pytest

from scripts import smoke_scrapers


class SyncScraper:
    last_error = None

    def scrape(self):
        return [object(), object()]


class ErrorScraper:
    last_error = "portal reset"

    def scrape(self):
        return []


class AsyncScraper:
    last_error = None

    async def scrape(self):
        return [object()]


def test_parse_states_accepts_aliases_and_all():
    assert smoke_scrapers.parse_states("texas,tn,ga,az") == [
        "texas",
        "tennessee",
        "georgia",
        "arizona",
    ]
    all_states = smoke_scrapers.parse_states("all")
    assert "texas" in all_states
    assert "tennessee" in all_states
    assert "florida" in all_states
    assert "georgia" in all_states
    assert "arizona" in all_states


@pytest.mark.asyncio
async def test_run_smoke_handles_sync_and_async_scrapers(monkeypatch):
    factories = {
        "texas": lambda lookback_days, headless: [("Harris", AsyncScraper())],
        "tennessee": lambda lookback_days, headless: [("Davidson", SyncScraper())],
    }

    result = await smoke_scrapers.run_smoke(
        states=["texas", "tennessee"],
        lookback_days=2,
        notify=False,
        factories=factories,
    )

    assert [(r.state, r.count, r.error) for r in result.results] == [
        ("texas", 1, None),
        ("tennessee", 2, None),
    ]
    assert result.pushover_sent is False


@pytest.mark.asyncio
async def test_run_smoke_sends_redacted_pushover_summary(monkeypatch):
    messages: list[tuple[str, str, dict[str, str] | None]] = []
    factories = {"tennessee": lambda lookback_days, headless: [("Davidson", ErrorScraper())]}

    async def send_alert(title, message, *, priority=0, tags=None):
        messages.append((title, message, tags))
        return True

    monkeypatch.setattr(smoke_scrapers.notification_service, "send_alert", send_alert)

    result = await smoke_scrapers.run_smoke(
        states=["tennessee"],
        lookback_days=2,
        notify=True,
        factories=factories,
    )

    assert result.pushover_sent is True
    assert result.results[0].error == "portal reset"
    assert messages == [
        (
            "Leadgen scraper smoke test",
            "Tennessee / Davidson: 0 filings (error: portal reset)",
            {"mode": "scraper-only", "runner": "not called"},
        )
    ]


def test_parse_states_recognises_cobb_alias():
    from scripts.smoke_scrapers import parse_states
    assert parse_states("cobb") == ["georgia_cobb"]
    assert parse_states("georgia_cobb") == ["georgia_cobb"]


def test_parse_states_recognises_dekalb_alias():
    from scripts.smoke_scrapers import parse_states
    assert parse_states("dekalb") == ["georgia_dekalb"]
    assert parse_states("georgia_dekalb") == ["georgia_dekalb"]


def test_parse_states_recognises_franklin_ohio_alias():
    from scripts.smoke_scrapers import parse_states
    assert parse_states("franklin_oh") == ["ohio_franklin"]
    assert parse_states("columbus") == ["ohio_franklin"]


def test_georgia_cobb_factory_returns_scraper():
    from scripts.smoke_scrapers import SCRAPER_FACTORIES
    scrapers = SCRAPER_FACTORIES["georgia_cobb"](7, True)
    assert len(scrapers) == 1
    label, scraper = scrapers[0]
    assert label == "Cobb Magistrate"
    assert scraper.enrich_addresses is False


def test_georgia_dekalb_factory_returns_scraper():
    from scripts.smoke_scrapers import SCRAPER_FACTORIES
    scrapers = SCRAPER_FACTORIES["georgia_dekalb"](7, True)
    assert len(scrapers) == 1
    label, scraper = scrapers[0]
    assert label == "DeKalb Magistrate"
    assert scraper.lookback_days == 7


def test_ohio_franklin_factory_returns_scraper():
    from scripts.smoke_scrapers import SCRAPER_FACTORIES
    scrapers = SCRAPER_FACTORIES["ohio_franklin"](7, True)
    assert len(scrapers) == 1
    label, scraper = scrapers[0]
    assert label == "Franklin Municipal"
    assert scraper.lookback_days == 7


def test_parse_states_recognises_hamilton_ohio_alias():
    from scripts.smoke_scrapers import parse_states
    assert parse_states("hamilton") == ["ohio_hamilton"]
    assert parse_states("cincinnati") == ["ohio_hamilton"]
    assert parse_states("hamilton_oh") == ["ohio_hamilton"]


def test_ohio_hamilton_factory_returns_scraper():
    from scripts.smoke_scrapers import SCRAPER_FACTORIES
    scrapers = SCRAPER_FACTORIES["ohio_hamilton"](7, True)
    assert len(scrapers) == 1
    label, scraper = scrapers[0]
    assert label == "Hamilton Municipal"
    assert scraper.lookback_days == 7


def test_parse_states_recognises_clark_nevada_alias():
    from scripts.smoke_scrapers import parse_states
    assert parse_states("clark") == ["nevada_clark"]
    assert parse_states("nevada") == ["nevada_clark"]
    assert parse_states("henderson") == ["nevada_clark"]
    assert parse_states("nevada_clark") == ["nevada_clark"]


def test_nevada_clark_factory_returns_scraper():
    from scripts.smoke_scrapers import SCRAPER_FACTORIES
    scrapers = SCRAPER_FACTORIES["nevada_clark"](7, True)
    assert len(scrapers) == 1
    label, scraper = scrapers[0]
    assert label == "Clark Justice Court"
    assert scraper.lookback_days == 7
    assert scraper.max_cases == 25
