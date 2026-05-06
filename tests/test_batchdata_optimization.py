from __future__ import annotations

from datetime import date

import pytest

from models.contact import EnrichedContact
from models.filing import Filing
from pipeline import runner
from services import batchdata_service


def _filing(**kwargs) -> Filing:
    values = {
        "case_number": "TEST-BATCHDATA-001",
        "tenant_name": "Jane Tenant",
        "property_address": "123 Main St, Houston, TX 77002",
        "landlord_name": "Grant Owner",
        "filing_date": date(2026, 5, 6),
        "state": "TX",
        "county": "Harris",
        "notice_type": "Eviction",
        "source_url": "https://example.test",
    }
    values.update(kwargs)
    return Filing(**values)


@pytest.mark.asyncio
async def test_runner_reuses_one_property_lookup_for_landlord_and_tenant(monkeypatch):
    filing = _filing(property_type_hint=None, claim_amount=None)
    calls: list[str] = []

    async def is_duplicate(case_number: str) -> bool:
        return False

    async def insert_filing(filing: Filing) -> None:
        return None

    async def update_classification(case_number: str, outcome) -> None:
        return None

    async def update_enrichment(contact: EnrichedContact) -> None:
        return None

    async def write_run_metrics(metrics: dict) -> None:
        calls.append(f"metrics:{metrics['batchdata_calls']}")

    async def normalize_address(address: str) -> str | None:
        return None

    async def lookup_property_info(filing: Filing) -> batchdata_service.PropertyInfo:
        calls.append("lookup")
        return batchdata_service.PropertyInfo(property_type="residential")

    async def enrich(filing: Filing, property_info=None, lookup_property_if_missing=True):
        calls.append(f"ec:{property_info.property_type}:{lookup_property_if_missing}")
        return EnrichedContact(
            filing=filing,
            track="ec",
            phone="+12135550100",
            property_type=property_info.property_type,
            dnc_status="clear",
        )

    async def enrich_tenant(filing: Filing, property_info=None, lookup_property_if_missing=True):
        calls.append(f"ng:{property_info.property_type}:{lookup_property_if_missing}")
        return EnrichedContact(
            filing=filing,
            track="ng",
            phone="+12135550101",
            property_type=property_info.property_type,
            dnc_status="clear",
        )

    async def process_track(contact: EnrichedContact) -> bool:
        return True

    monkeypatch.setattr(runner, "_NG_ENABLED", True)
    monkeypatch.setattr(runner.dedup_service, "is_duplicate", is_duplicate)
    monkeypatch.setattr(runner.dedup_service, "insert_filing", insert_filing)
    monkeypatch.setattr(runner.dedup_service, "update_classification", update_classification)
    monkeypatch.setattr(runner.dedup_service, "update_enrichment", update_enrichment)
    monkeypatch.setattr(runner.dedup_service, "write_run_metrics", write_run_metrics)
    monkeypatch.setattr(runner.geocode_service, "normalize_address", normalize_address)
    monkeypatch.setattr(runner.batchdata_service, "lookup_property_info", lookup_property_info)
    monkeypatch.setattr(runner.batchdata_service, "enrich", enrich)
    monkeypatch.setattr(runner.batchdata_service, "enrich_tenant", enrich_tenant)
    monkeypatch.setattr(runner, "_process_track", process_track)

    await runner.run([filing], state="TX", county="Harris")

    assert calls.count("lookup") == 1
    assert "ec:residential:False" in calls
    assert "ng:residential:False" in calls
    assert "metrics:3" in calls


@pytest.mark.asyncio
async def test_runner_skips_property_lookup_when_scraper_supplies_type(monkeypatch):
    filing = _filing(property_type_hint="residential", claim_amount=None)
    calls: list[str] = []

    async def is_duplicate(case_number: str) -> bool:
        return False

    async def no_lookup(filing: Filing):
        raise AssertionError("property lookup should not run when scraper supplies type")

    async def enrich(filing: Filing, property_info=None, lookup_property_if_missing=True):
        calls.append(f"ec:{property_info}:{lookup_property_if_missing}")
        return EnrichedContact(
            filing=filing,
            track="ec",
            phone="+12135550100",
            property_type=filing.property_type_hint,
            dnc_status="clear",
        )

    async def process_track(contact: EnrichedContact) -> bool:
        return True

    async def write_run_metrics(metrics: dict) -> None:
        calls.append(f"metrics:{metrics['batchdata_calls']}")

    monkeypatch.setattr(runner, "_NG_ENABLED", False)
    monkeypatch.setattr(runner.dedup_service, "is_duplicate", is_duplicate)
    monkeypatch.setattr(runner.dedup_service, "insert_filing", _async_none)
    monkeypatch.setattr(runner.dedup_service, "update_classification", _async_none)
    monkeypatch.setattr(runner.dedup_service, "update_enrichment", _async_none)
    monkeypatch.setattr(runner.dedup_service, "write_run_metrics", write_run_metrics)
    monkeypatch.setattr(runner.geocode_service, "normalize_address", _async_none)
    monkeypatch.setattr(runner.batchdata_service, "lookup_property_info", no_lookup)
    monkeypatch.setattr(runner.batchdata_service, "enrich", enrich)
    monkeypatch.setattr(runner, "_process_track", process_track)

    await runner.run([filing], state="TX", county="Harris")

    assert "ec:None:False" in calls
    assert "metrics:1" in calls


@pytest.mark.asyncio
async def test_runner_alerts_when_enrichment_fails(monkeypatch):
    filing = _filing(property_type_hint="residential", claim_amount=None)
    alerts: list[tuple[str, str, str]] = []

    async def is_duplicate(case_number: str) -> bool:
        return False

    async def enrich(*args, **kwargs):
        raise RuntimeError("BatchData timeout")

    async def send_job_error(*, job: str, stage: str, error, priority: int = 1):
        alerts.append((job, stage, str(error)))
        return True

    monkeypatch.setattr(runner, "_NG_ENABLED", False)
    monkeypatch.setattr(runner.dedup_service, "is_duplicate", is_duplicate)
    monkeypatch.setattr(runner.dedup_service, "insert_filing", _async_none)
    monkeypatch.setattr(runner.dedup_service, "update_classification", _async_none)
    monkeypatch.setattr(runner.dedup_service, "write_run_metrics", _async_none)
    monkeypatch.setattr(runner.geocode_service, "normalize_address", _async_none)
    monkeypatch.setattr(runner.batchdata_service, "enrich", enrich)
    monkeypatch.setattr(runner.notification_service, "send_job_error", send_job_error)

    await runner.run([filing], state="TX", county="Harris")

    assert alerts == [("TX/Harris", "batchdata_enrichment", "BatchData timeout")]


async def _async_none(*args, **kwargs):
    return None
