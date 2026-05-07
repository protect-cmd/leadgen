from __future__ import annotations

from datetime import date

import pytest

from models.filing import Filing
from pipeline import runner


def _filing() -> Filing:
    return Filing(
        case_number="TEST-RUN-SUMMARY",
        tenant_name="Jane Tenant",
        property_address="123 Main St, Houston, TX 77002",
        landlord_name="Grant Owner",
        filing_date=date(2026, 5, 7),
        state="TX",
        county="Harris",
        notice_type="Eviction",
        source_url="https://example.test",
    )


@pytest.mark.asyncio
async def test_runner_sends_success_summary_after_metrics(monkeypatch):
    calls: list[str] = []

    async def is_duplicate(case_number: str) -> bool:
        return True

    async def write_run_metrics(metrics: dict) -> None:
        calls.append(f"metrics:{metrics['duplicates_skipped']}")

    async def send_run_summary(metrics: dict, *, auto_bland_enabled: bool) -> bool:
        calls.append(
            f"summary:{metrics['state']}:{metrics['county']}:"
            f"{metrics['filings_received']}:{auto_bland_enabled}"
        )
        return True

    monkeypatch.setattr(runner.dedup_service, "is_duplicate", is_duplicate)
    monkeypatch.setattr(runner.dedup_service, "write_run_metrics", write_run_metrics)
    monkeypatch.setattr(runner.notification_service, "send_run_summary", send_run_summary)
    monkeypatch.setattr(runner, "_AUTO_BLAND_CALLS_ENABLED", False)

    await runner.run([_filing()], state="TX", county="Harris")

    assert calls == ["metrics:1", "summary:TX:Harris:1:False"]
