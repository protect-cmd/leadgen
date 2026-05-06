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
    assert smoke_scrapers.parse_states("texas,tn") == ["texas", "tennessee"]
    assert smoke_scrapers.parse_states("all") == ["texas", "tennessee"]


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
