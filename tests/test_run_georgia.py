from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from jobs import run_georgia
from models.filing import Filing


class FakeGeorgiaScraper:
    instances: list["FakeGeorgiaScraper"] = []

    def __init__(self, lookback_days: int, hearing_lookahead_days: int):
        self.lookback_days = lookback_days
        self.hearing_lookahead_days = hearing_lookahead_days
        self.instances.append(self)

    async def scrape(self) -> list[Filing]:
        return [
            Filing(
                case_number="2026-GA-001",
                tenant_name="Jane Tenant",
                property_address="Unknown",
                landlord_name="Acme Landlord LLC",
                filing_date=date(2026, 5, 12),
                court_date=date(2026, 5, 20),
                state="GA",
                county="Fulton",
                notice_type="Dispossessory",
                source_url="https://example.com/georgia-case",
            )
        ]


def test_georgia_summary_marks_default_as_scraper_only():
    summary = run_georgia.build_summary(
        filings=[
            Filing(
                case_number="2026-GA-001",
                tenant_name="Jane Tenant",
                property_address="Unknown",
                landlord_name="Acme Landlord LLC",
                filing_date=date(2026, 5, 12),
                court_date=date(2026, 5, 20),
                state="GA",
                county="Fulton",
                notice_type="Dispossessory",
                source_url="https://example.com/georgia-case",
            )
        ],
        lookback_days=2,
        hearing_lookahead_days=45,
        piped=False,
    )

    lines = summary.to_lines()

    assert "Georgia / re:SearchGA scraper-only proof" in lines[0]
    assert "Tenant enrichment: pending Melissa Personator integration" in lines
    assert "Runner/enrichment/outreach: not called (scraper-only mode)" in lines[-1]


@pytest.mark.asyncio
async def test_georgia_job_defaults_to_scraper_only(monkeypatch, capsys):
    calls: list[tuple[list[Filing], str, str]] = []
    FakeGeorgiaScraper.instances = []

    async def fake_run(filings: list[Filing], *, state: str = "", county: str = "") -> None:
        calls.append((filings, state, county))

    monkeypatch.setattr(run_georgia, "ReSearchGAScraper", FakeGeorgiaScraper)
    monkeypatch.setattr(run_georgia, "runner", SimpleNamespace(run=fake_run), raising=False)

    summary = await run_georgia.main(lookback_days=3, hearing_lookahead_days=60)

    assert summary.total_filings == 1
    assert summary.piped is False
    assert calls == []
    assert FakeGeorgiaScraper.instances[0].lookback_days == 3
    assert FakeGeorgiaScraper.instances[0].hearing_lookahead_days == 60
    assert "Runner/enrichment/outreach: not called" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_georgia_job_pipe_mode_is_explicit(monkeypatch, capsys):
    calls: list[tuple[list[Filing], str, str]] = []
    FakeGeorgiaScraper.instances = []

    async def fake_run(filings: list[Filing], *, state: str = "", county: str = "") -> None:
        calls.append((filings, state, county))

    monkeypatch.setattr(run_georgia, "ReSearchGAScraper", FakeGeorgiaScraper)
    monkeypatch.setattr(run_georgia, "runner", SimpleNamespace(run=fake_run), raising=False)

    summary = await run_georgia.main(pipe=True)

    assert summary.piped is True
    assert len(calls) == 1
    filings, state, county = calls[0]
    assert len(filings) == 1
    assert state == "GA"
    assert county == "re:SearchGA"
    assert "Georgia / re:SearchGA pipeline run" in capsys.readouterr().out
