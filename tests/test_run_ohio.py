from __future__ import annotations

from datetime import date

import pytest

from jobs import run_ohio
from models.filing import Filing


def _make_filing(case_number: str, county: str, address: str) -> Filing:
    return Filing(
        case_number=case_number,
        tenant_name="Test Tenant",
        property_address=address,
        landlord_name="Test Landlord LLC",
        filing_date=date(2026, 5, 16),
        court_date=date(2026, 5, 23),
        state="OH",
        county=county,
        notice_type="Eviction",
        source_url=f"https://example.com/{case_number}",
    )


class FakeFranklinScraper:
    def __init__(self, *, lookback_days: int):
        self.lookback_days = lookback_days

    def scrape(self) -> list[Filing]:
        return [_make_filing("FR-001", "Franklin", "100 S HIGH ST, COLUMBUS, OH 43215")]


class FakeHamiltonScraper:
    def __init__(self, *, lookback_days: int):
        self.lookback_days = lookback_days

    def scrape(self) -> list[Filing]:
        return [_make_filing("HA-001", "Hamilton", "456 ELM ST, CINCINNATI, OH 45219")]


class EmptyScraper:
    def __init__(self, *, lookback_days: int):
        pass

    def scrape(self) -> list[Filing]:
        return []


# ---------------------------------------------------------------------------
# Summary / to_lines() unit tests
# ---------------------------------------------------------------------------


def test_summary_scraper_only_mode():
    summary = run_ohio.OhioRunSummary(
        franklin_filings=3,
        hamilton_filings=2,
        piped=False,
    )
    lines = summary.to_lines()
    assert lines[0] == "Ohio scraper-only proof"
    assert "Franklin Municipal (Columbus): 3 filings" in lines
    assert "Hamilton Municipal (Cincinnati): 2 filings" in lines
    assert "Total: 5" in lines
    assert "Runner/enrichment/outreach: not called (scraper-only mode)" in lines[-1]
    # No stale "proof-only" or "Melissa" language
    assert not any("proof only" in ln for ln in lines)
    assert not any("Melissa" in ln for ln in lines)


def test_summary_pipeline_mode():
    summary = run_ohio.OhioRunSummary(
        franklin_filings=4,
        hamilton_filings=3,
        piped=True,
    )
    lines = summary.to_lines()
    assert lines[0] == "Ohio pipeline run"
    assert "Runner: called with 7 filings" in lines[-1]


# ---------------------------------------------------------------------------
# main() integration tests (scrapers mocked)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_main_scraper_only_returns_correct_counts(monkeypatch, capsys):
    monkeypatch.setattr(run_ohio, "FranklinCountyMunicipalScraper", FakeFranklinScraper)
    monkeypatch.setattr(run_ohio, "HamiltonCountyMunicipalScraper", FakeHamiltonScraper)

    summary = await run_ohio.main(pipe=False)

    assert summary.franklin_filings == 1
    assert summary.hamilton_filings == 1
    assert summary.piped is False
    output = capsys.readouterr().out
    assert "Ohio scraper-only proof" in output
    assert "Runner/enrichment/outreach: not called" in output


@pytest.mark.asyncio
async def test_main_counties_filter_franklin_only(monkeypatch):
    monkeypatch.setattr(run_ohio, "FranklinCountyMunicipalScraper", FakeFranklinScraper)
    monkeypatch.setattr(run_ohio, "HamiltonCountyMunicipalScraper", FakeHamiltonScraper)

    summary = await run_ohio.main(pipe=False, counties=["franklin"])

    assert summary.franklin_filings == 1
    assert summary.hamilton_filings == 0


@pytest.mark.asyncio
async def test_main_counties_filter_hamilton_only(monkeypatch):
    monkeypatch.setattr(run_ohio, "FranklinCountyMunicipalScraper", FakeFranklinScraper)
    monkeypatch.setattr(run_ohio, "HamiltonCountyMunicipalScraper", FakeHamiltonScraper)

    summary = await run_ohio.main(pipe=False, counties=["hamilton"])

    assert summary.franklin_filings == 0
    assert summary.hamilton_filings == 1


@pytest.mark.asyncio
async def test_franklin_piped_when_pipe_flag(monkeypatch, capsys):
    piped_calls: list[tuple[list[Filing], str, str]] = []

    async def fake_pipeline_run(filings, *, state, county):
        piped_calls.append((list(filings), state, county))

    monkeypatch.setattr(run_ohio, "FranklinCountyMunicipalScraper", FakeFranklinScraper)
    monkeypatch.setattr(run_ohio, "HamiltonCountyMunicipalScraper", EmptyScraper)

    import pipeline.runner as pipeline_runner
    monkeypatch.setattr(pipeline_runner, "run", fake_pipeline_run)

    summary = await run_ohio.main(pipe=True, counties=["franklin"])

    assert summary.piped is True
    assert summary.franklin_filings == 1
    assert len(piped_calls) == 1
    _, state, county = piped_calls[0]
    assert state == "OH"
    assert county == "Franklin"
    output = capsys.readouterr().out
    assert "Ohio pipeline run" in output


@pytest.mark.asyncio
async def test_hamilton_piped_when_pipe_flag(monkeypatch, capsys):
    """When --pipe is set and Hamilton has filings, pipeline_runner.run is called for Hamilton."""
    piped_calls: list[tuple[list[Filing], str, str]] = []

    async def fake_pipeline_run(filings, *, state, county):
        piped_calls.append((list(filings), state, county))

    monkeypatch.setattr(run_ohio, "FranklinCountyMunicipalScraper", EmptyScraper)
    monkeypatch.setattr(run_ohio, "HamiltonCountyMunicipalScraper", FakeHamiltonScraper)

    import pipeline.runner as pipeline_runner
    monkeypatch.setattr(pipeline_runner, "run", fake_pipeline_run)

    summary = await run_ohio.main(pipe=True, counties=["hamilton"])

    assert summary.piped is True
    assert summary.hamilton_filings == 1
    assert len(piped_calls) == 1
    filings, state, county = piped_calls[0]
    assert len(filings) == 1
    assert filings[0].case_number == "HA-001"
    assert state == "OH"
    assert county == "Hamilton"
    output = capsys.readouterr().out
    assert "Ohio pipeline run" in output


@pytest.mark.asyncio
async def test_hamilton_not_piped_when_no_pipe_flag(monkeypatch):
    """When --pipe is not set, Hamilton filings are scraped but not piped."""
    piped_calls: list = []

    async def fake_pipeline_run(filings, *, state, county):
        piped_calls.append((filings, state, county))

    monkeypatch.setattr(run_ohio, "FranklinCountyMunicipalScraper", EmptyScraper)
    monkeypatch.setattr(run_ohio, "HamiltonCountyMunicipalScraper", FakeHamiltonScraper)

    import pipeline.runner as pipeline_runner
    monkeypatch.setattr(pipeline_runner, "run", fake_pipeline_run)

    summary = await run_ohio.main(pipe=False, counties=["hamilton"])

    assert summary.piped is False
    assert summary.hamilton_filings == 1
    assert piped_calls == []


@pytest.mark.asyncio
async def test_both_counties_piped_when_pipe_flag(monkeypatch):
    """When --pipe is set and both counties have filings, both are piped."""
    piped_calls: list[tuple[list[Filing], str, str]] = []

    async def fake_pipeline_run(filings, *, state, county):
        piped_calls.append((list(filings), state, county))

    monkeypatch.setattr(run_ohio, "FranklinCountyMunicipalScraper", FakeFranklinScraper)
    monkeypatch.setattr(run_ohio, "HamiltonCountyMunicipalScraper", FakeHamiltonScraper)

    import pipeline.runner as pipeline_runner
    monkeypatch.setattr(pipeline_runner, "run", fake_pipeline_run)

    summary = await run_ohio.main(pipe=True)

    assert summary.piped is True
    assert summary.franklin_filings == 1
    assert summary.hamilton_filings == 1
    assert len(piped_calls) == 2
    counties_piped = {county for _, _, county in piped_calls}
    assert counties_piped == {"Franklin", "Hamilton"}
