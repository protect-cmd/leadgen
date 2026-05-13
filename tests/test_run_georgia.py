from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from jobs import run_georgia
from models.filing import Filing


class FakeGeorgiaScraper:
    def __init__(self, lookback_days: int):
        self.lookback_days = lookback_days

    async def scrape(self) -> list[Filing]:
        return [
            Filing(
                case_number="2026-GA-001",
                tenant_name="Jane Tenant",
                property_address="123 Peachtree St NE, Atlanta, GA 30308",
                landlord_name="Acme Landlord LLC",
                filing_date=date(2026, 5, 12),
                court_date=None,
                state="GA",
                county="Fulton",
                notice_type="Dispossessory",
                source_url="https://example.com/georgia-case",
            )
        ]


@pytest.mark.asyncio
async def test_georgia_job_sends_filings_to_runner(monkeypatch):
    calls: list[tuple[list[Filing], str, str]] = []

    async def fake_run(filings: list[Filing], *, state: str = "", county: str = "") -> None:
        calls.append((filings, state, county))

    monkeypatch.setattr(run_georgia, "ReSearchGAScraper", FakeGeorgiaScraper)
    monkeypatch.setattr(run_georgia, "runner", SimpleNamespace(run=fake_run), raising=False)

    await run_georgia.main()

    assert len(calls) == 1
    filings, state, county = calls[0]
    assert len(filings) == 1
    assert state == "GA"
    assert county == "re:SearchGA"
