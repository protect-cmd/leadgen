from __future__ import annotations

from datetime import date

import pytest

from jobs import run_arizona
from models.filing import Filing
from scrapers.arizona.maricopa_assessor import AddressMatchResult


class FakeArizonaScraper:
    def __init__(self, *, lookback_days: int, max_cases: int, enrich_addresses: bool):
        self.lookback_days = lookback_days
        self.max_cases = max_cases
        self.enrich_addresses = enrich_addresses
        self.address_match_counts = {
            "single_match": 1,
            "ambiguous": 1,
            "no_match": 1,
            "error": 0,
        }
        self.address_matches_by_case: dict[str, AddressMatchResult] = {
            "CC2026000001": AddressMatchResult(status="single_match"),
            "CC2026000002": AddressMatchResult(status="ambiguous"),
            "CC2026000003": AddressMatchResult(status="no_match"),
        }

    def scrape(self) -> list[Filing]:
        return [
            Filing(
                case_number="CC2026000001",
                tenant_name="Tenant One",
                property_address="123 W MAIN ST PHOENIX 85001",
                landlord_name="Single Owner LLC",
                filing_date=date(2026, 5, 11),
                court_date=date(2026, 5, 15),
                state="AZ",
                county="Maricopa",
                notice_type="Eviction Action Hearing",
                source_url="https://example.com/1",
            ),
            Filing(
                case_number="CC2026000002",
                tenant_name="Tenant Two",
                property_address="Unknown",
                landlord_name="Portfolio Owner LLC",
                filing_date=date(2026, 5, 11),
                court_date=date(2026, 5, 15),
                state="AZ",
                county="Maricopa",
                notice_type="Eviction Action Hearing",
                source_url="https://example.com/2",
            ),
            Filing(
                case_number="CC2026000003",
                tenant_name="Tenant Three",
                property_address="Unknown",
                landlord_name="Missing Owner LLC",
                filing_date=date(2026, 5, 11),
                court_date=date(2026, 5, 15),
                state="AZ",
                county="Maricopa",
                notice_type="Eviction Action Hearing",
                source_url="https://example.com/3",
            ),
        ]


def test_build_summary_counts_only_single_matches_as_usable():
    summary = run_arizona.build_summary(
        filings=[
            Filing(
                case_number="CC2026000001",
                tenant_name="Tenant One",
                property_address="123 W MAIN ST PHOENIX 85001",
                landlord_name="Single Owner LLC",
                filing_date=date(2026, 5, 11),
                court_date=None,
                state="AZ",
                county="Maricopa",
                notice_type="Eviction Action Hearing",
                source_url="https://example.com/1",
            )
        ],
        address_match_counts={
            "single_match": 1,
            "ambiguous": 2,
            "no_match": 3,
            "error": 0,
        },
        max_cases=50,
        lookback_days=7,
        piped=False,
    )

    assert summary.total_filings == 1
    assert summary.usable_single_match == 1
    assert summary.held_for_review == 5
    assert summary.to_lines() == [
        "Arizona / Maricopa scraper-only proof",
        "Max cases: 50",
        "Lookback days: 7",
        "Total filings: 1",
        "Usable single-match addresses: 1",
        "Held for review: 5",
        "Ambiguous owner matches: 2",
        "No owner match: 3",
        "Match errors: 0",
        "Runner/enrichment/outreach: not called (scraper-only mode)",
    ]


@pytest.mark.asyncio
async def test_main_runs_scraper_only_summary(monkeypatch, capsys):
    monkeypatch.setattr(run_arizona, "MaricopaJusticeCourtScraper", FakeArizonaScraper)

    summary = await run_arizona.main(max_cases=50, lookback_days=7, notify=False)

    assert summary.total_filings == 3
    assert summary.usable_single_match == 1
    assert summary.held_for_review == 2
    assert not hasattr(run_arizona, "runner")
    output = capsys.readouterr().out
    assert "Arizona / Maricopa scraper-only proof" in output
    assert "Runner/enrichment/outreach: not called (scraper-only mode)" in output


@pytest.mark.asyncio
async def test_main_defaults_to_two_day_daily_lookback(monkeypatch):
    monkeypatch.setattr(run_arizona, "MaricopaJusticeCourtScraper", FakeArizonaScraper)

    summary = await run_arizona.main(notify=False)

    assert summary.lookback_days == 2


@pytest.mark.asyncio
async def test_main_pipe_mode_filters_to_single_match_only(monkeypatch, capsys):
    piped_filings: list[Filing] = []

    async def fake_pipeline_run(filings, *, state, county):
        piped_filings.extend(filings)

    monkeypatch.setattr(run_arizona, "MaricopaJusticeCourtScraper", FakeArizonaScraper)

    import pipeline.runner as pipeline_runner
    monkeypatch.setattr(pipeline_runner, "run", fake_pipeline_run)

    summary = await run_arizona.main(max_cases=50, lookback_days=7, notify=False, pipe=True)

    assert summary.piped is True
    assert summary.total_filings == 3
    assert len(piped_filings) == 1
    assert piped_filings[0].case_number == "CC2026000001"
    output = capsys.readouterr().out
    assert "Arizona / Maricopa pipeline run" in output
    assert "Runner: called with 1 single-match filings" in output
