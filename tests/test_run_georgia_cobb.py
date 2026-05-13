from __future__ import annotations

from collections import Counter
from datetime import date

import pytest

from jobs import run_georgia_cobb
from models.filing import Filing
from scrapers.georgia.cobb_assessor import AddressMatchResult


def _filing(case_number: str, property_address: str = "123 Main St, Marietta, GA 30060") -> Filing:
    return Filing(
        case_number=case_number,
        tenant_name="Tenant",
        property_address=property_address,
        landlord_name="Landlord LLC",
        filing_date=date(2026, 5, 15),
        court_date=date(2026, 5, 15),
        state="GA",
        county="Cobb",
        notice_type="Dispossessory",
        source_url="https://example.com",
    )


class FakeCobbScraper:
    def __init__(self, *, lookback_days: int, max_cases: int, enrich_addresses: bool):
        self.lookback_days = lookback_days
        self.max_cases = max_cases
        self.enrich_addresses = enrich_addresses
        self.address_match_counts = Counter({
            "single_match": 1,
            "ambiguous": 1,
            "no_match": 1,
            "error": 0,
        })
        self.address_matches_by_case: dict[str, AddressMatchResult] = {
            "26MD000001": AddressMatchResult(status="single_match"),
            "26MD000002": AddressMatchResult(status="ambiguous"),
            "26MD000003": AddressMatchResult(status="no_match"),
        }

    def scrape(self) -> list[Filing]:
        return [
            _filing("26MD000001", "100 Oak St, Marietta, GA 30060"),
            _filing("26MD000002", "Unknown"),
            _filing("26MD000003", "Unknown"),
        ]


def test_build_summary_counts_single_match_as_usable():
    summary = run_georgia_cobb.build_summary(
        filings=[_filing("26MD000001")],
        address_match_counts=Counter({"single_match": 1, "ambiguous": 2, "no_match": 3}),
        max_cases=100,
        lookback_days=30,
        piped=False,
    )
    assert summary.total_filings == 1
    assert summary.usable_single_match == 1
    assert summary.held_for_review == 5
    lines = summary.to_lines()
    assert "Georgia / Cobb scraper-only proof" in lines[0]
    assert "Runner/enrichment/outreach: not called (scraper-only mode)" in lines[-1]


@pytest.mark.asyncio
async def test_main_scraper_only_mode(monkeypatch, capsys):
    monkeypatch.setattr(run_georgia_cobb, "CobbMagistrateCourtScraper", FakeCobbScraper)
    summary = await run_georgia_cobb.main(max_cases=100, lookback_days=30, notify=False)
    assert summary.total_filings == 3
    assert summary.usable_single_match == 1
    assert not summary.piped
    out = capsys.readouterr().out
    assert "Georgia / Cobb scraper-only proof" in out


@pytest.mark.asyncio
async def test_main_pipe_mode_sends_only_single_match(monkeypatch, capsys):
    piped_filings: list[Filing] = []

    async def fake_run(filings, *, state, county):
        piped_filings.extend(filings)

    monkeypatch.setattr(run_georgia_cobb, "CobbMagistrateCourtScraper", FakeCobbScraper)

    import pipeline.runner as pipeline_runner
    monkeypatch.setattr(pipeline_runner, "run", fake_run)

    summary = await run_georgia_cobb.main(max_cases=100, lookback_days=30, notify=False, pipe=True)

    assert summary.piped is True
    assert len(piped_filings) == 1
    assert piped_filings[0].case_number == "26MD000001"
    out = capsys.readouterr().out
    assert "Georgia / Cobb pipeline run" in out
    assert "Runner: called with 1 single-match filings" in out
