from __future__ import annotations

from datetime import date

import pytest

from jobs import run_georgia_dekalb
from models.filing import Filing


def _filing(case_number: str = "26D08231") -> Filing:
    return Filing(
        case_number=case_number,
        tenant_name="Gloria Gooden",
        property_address="Unknown",
        landlord_name="Amani Place Apartments",
        filing_date=date(2026, 5, 12),
        court_date=date(2026, 5, 12),
        state="GA",
        county="DeKalb",
        notice_type="Dispossessory",
        source_url="https://example.test/dispo.pdf",
    )


class FakeDeKalbScraper:
    def __init__(self, *, lookback_days: int, max_cases: int):
        self.lookback_days = lookback_days
        self.max_cases = max_cases

    def scrape(self) -> list[Filing]:
        return [_filing()]


@pytest.mark.asyncio
async def test_main_scraper_only_summary_does_not_call_pipeline(monkeypatch, capsys):
    monkeypatch.setattr(run_georgia_dekalb, "DeKalbDispossessoryScraper", FakeDeKalbScraper)

    summary = await run_georgia_dekalb.main(max_cases=50, lookback_days=2, notify=False)

    assert summary.total_filings == 1
    assert summary.piped is False
    assert not hasattr(run_georgia_dekalb, "runner")
    out = capsys.readouterr().out
    assert "Georgia / DeKalb scraper-only proof" in out
    assert "Runner/enrichment/outreach: not called (scraper-only mode)" in out


@pytest.mark.asyncio
async def test_main_defaults_to_two_day_daily_lookback(monkeypatch):
    monkeypatch.setattr(run_georgia_dekalb, "DeKalbDispossessoryScraper", FakeDeKalbScraper)

    summary = await run_georgia_dekalb.main(notify=False)

    assert summary.lookback_days == 2


@pytest.mark.asyncio
async def test_main_pipe_mode_sends_filings_to_pipeline(monkeypatch, capsys):
    piped_filings: list[Filing] = []

    async def fake_run(filings, *, state, county):
        piped_filings.extend(filings)

    monkeypatch.setattr(run_georgia_dekalb, "DeKalbDispossessoryScraper", FakeDeKalbScraper)

    import pipeline.runner as pipeline_runner

    monkeypatch.setattr(pipeline_runner, "run", fake_run)

    summary = await run_georgia_dekalb.main(max_cases=50, lookback_days=2, notify=False, pipe=True)

    assert summary.piped is True
    assert len(piped_filings) == 1
    assert piped_filings[0].case_number == "26D08231"
    out = capsys.readouterr().out
    assert "Georgia / DeKalb pipeline run" in out
    assert "Runner: called with 1 filings" in out
