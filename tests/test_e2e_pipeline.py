from __future__ import annotations

from datetime import date

import pytest

from models.contact import EnrichedContact
from models.filing import Filing
from pipeline import runner


def _filing(**kwargs) -> Filing:
    values = {
        "case_number": "TEST-E2E-001",
        "tenant_name": "Jane Tenant",
        "property_address": "123 Main St, Houston, TX 77002",
        "landlord_name": "Grant Owner",
        "filing_date": date(2026, 5, 6),
        "state": "TX",
        "county": "Harris",
        "notice_type": "Eviction",
        "source_url": "https://example.test",
        "claim_amount": 2000.0,
        "property_type_hint": "residential",
    }
    values.update(kwargs)
    return Filing(**values)


def _contact(filing: Filing, *, track: str = "ec", dnc_status: str = "clear") -> EnrichedContact:
    return EnrichedContact(
        filing=filing,
        track=track,
        phone="+12135550100" if track == "ec" else "+12135550101",
        email=f"{track}@example.test",
        estimated_rent=2000,
        property_type="residential",
        dnc_status=dnc_status,
        dnc_source="test",
    )


@pytest.mark.asyncio
async def test_pipeline_happy_path_ec_queues_bland_without_auto_call(monkeypatch):
    filing = _filing()
    calls: list[tuple] = []

    async def enrich(filing: Filing, **kwargs):
        calls.append(("enrich", filing.case_number, kwargs["lookup_property_if_missing"]))
        return _contact(filing)

    async def create_contact(contact: EnrichedContact, tags: list[str], pipeline_stage_id: str):
        calls.append(("ghl", contact.track, tags, pipeline_stage_id))
        return "ghl-ec"

    async def update_enrichment(contact: EnrichedContact):
        calls.append(("enrichment_saved", contact.track, contact.dnc_status))

    async def set_bland_status(case_number: str, track: str, status: str, call_id: str | None = None):
        calls.append(("bland_status", case_number, track, status, call_id))

    async def trigger_voicemail(contact: EnrichedContact):
        raise AssertionError("Auto Bland is disabled; no call should be triggered")

    monkeypatch.setenv("INSTANTLY_ENABLED", "false")
    monkeypatch.setenv("TENANT_TRACK_ENABLED", "false")
    monkeypatch.setenv("LANDLORD_TRACK_ENABLED", "true")
    monkeypatch.setattr(runner, "_AUTO_BLAND_CALLS_ENABLED", False)
    monkeypatch.setattr(runner, "GHL_EC_STAGE_ID", "stage-ec")
    _mock_common_runner_services(monkeypatch, calls)
    monkeypatch.setattr(runner.batchdata_service, "enrich", enrich)
    monkeypatch.setattr(runner.ghl_service, "create_contact", create_contact)
    monkeypatch.setattr(runner.dedup_service, "update_enrichment", update_enrichment)
    monkeypatch.setattr(runner.dedup_service, "set_bland_status", set_bland_status)
    monkeypatch.setattr(runner.bland_service, "trigger_voicemail", trigger_voicemail)

    await runner.run([filing], state="TX", county="Harris")

    assert ("insert", "TEST-E2E-001") in calls
    assert ("enrich", "TEST-E2E-001", False) in calls
    assert ("enrichment_saved", "ec", "clear") in calls
    assert ("ghl", "ec", ["EC-New-Filing"], "stage-ec") in calls
    assert ("ghl_id", "TEST-E2E-001", "ec", "ghl-ec") in calls
    assert ("bland_status", "TEST-E2E-001", "ec", "pending", None) in calls


@pytest.mark.asyncio
async def test_pipeline_dnc_blocked_never_triggers_bland(monkeypatch):
    filing = _filing()
    calls: list[tuple] = []

    async def enrich(filing: Filing, **kwargs):
        return _contact(filing, dnc_status="blocked")

    async def create_contact(contact: EnrichedContact, tags: list[str], pipeline_stage_id: str):
        calls.append(("ghl", contact.track))
        return "ghl-ec"

    async def set_bland_status(case_number: str, track: str, status: str, call_id: str | None = None):
        calls.append(("bland_status", track, status, call_id))

    async def trigger_voicemail(contact: EnrichedContact):
        raise AssertionError("DNC-blocked contacts must never trigger Bland")

    monkeypatch.setenv("INSTANTLY_ENABLED", "false")
    monkeypatch.setenv("TENANT_TRACK_ENABLED", "false")
    monkeypatch.setenv("LANDLORD_TRACK_ENABLED", "true")
    monkeypatch.setattr(runner, "_AUTO_BLAND_CALLS_ENABLED", True)
    monkeypatch.setattr(runner, "GHL_EC_STAGE_ID", "stage-ec")
    _mock_common_runner_services(monkeypatch, calls)
    monkeypatch.setattr(runner.batchdata_service, "enrich", enrich)
    monkeypatch.setattr(runner.ghl_service, "create_contact", create_contact)
    monkeypatch.setattr(runner.dedup_service, "set_bland_status", set_bland_status)
    monkeypatch.setattr(runner.bland_service, "trigger_voicemail", trigger_voicemail)

    await runner.run([filing], state="TX", county="Harris")

    assert ("ghl", "ec") in calls
    assert ("bland_status", "ec", "blocked_dnc", None) in calls


@pytest.mark.asyncio
async def test_pipeline_ec_and_ng_tracks_are_processed_separately(monkeypatch):
    filing = _filing(case_number="TEST-E2E-002")
    calls: list[tuple] = []

    async def enrich(filing: Filing, **kwargs):
        return _contact(filing, track="ec")

    async def enrich_tenant(filing: Filing, **kwargs):
        return _contact(filing, track="ng")

    async def create_contact(contact: EnrichedContact, tags: list[str], pipeline_stage_id: str):
        calls.append(("ghl", contact.track, tags, pipeline_stage_id))
        return f"ghl-{contact.track}"

    async def update_enrichment(contact: EnrichedContact):
        calls.append(("enrichment_saved", contact.track))

    async def set_bland_status(case_number: str, track: str, status: str, call_id: str | None = None):
        calls.append(("bland_status", track, status))

    monkeypatch.setenv("INSTANTLY_ENABLED", "false")
    monkeypatch.setenv("TENANT_TRACK_ENABLED", "true")
    monkeypatch.setenv("LANDLORD_TRACK_ENABLED", "true")
    monkeypatch.setattr(runner, "_AUTO_BLAND_CALLS_ENABLED", False)
    monkeypatch.setattr(runner, "GHL_EC_STAGE_ID", "stage-ec")
    monkeypatch.setattr(runner, "GHL_NG_RESIDENTIAL_STAGE_ID", "stage-ng")
    _mock_common_runner_services(monkeypatch, calls)
    monkeypatch.setattr(runner.batchdata_service, "enrich", enrich)
    monkeypatch.setattr(runner.batchdata_service, "enrich_tenant", enrich_tenant)
    monkeypatch.setattr(runner.ghl_service, "create_contact", create_contact)
    monkeypatch.setattr(runner.dedup_service, "update_enrichment", update_enrichment)
    monkeypatch.setattr(runner.dedup_service, "set_bland_status", set_bland_status)

    await runner.run([filing], state="TX", county="Harris")

    assert ("enrichment_saved", "ec") in calls
    assert ("enrichment_saved", "ng") in calls
    assert ("ghl", "ec", ["EC-New-Filing"], "stage-ec") in calls
    assert ("ghl", "ng", ["NG-New-Filing"], "stage-ng") in calls
    assert ("ghl_id", "TEST-E2E-002", "ec", "ghl-ec") in calls
    assert ("ghl_id", "TEST-E2E-002", "ng", "ghl-ng") in calls
    assert ("bland_status", "ec", "pending") in calls
    assert ("bland_status", "ng", "pending") in calls


def _mock_common_runner_services(monkeypatch, calls: list[tuple]) -> None:
    async def is_duplicate(case_number: str) -> bool:
        return False

    async def insert_filing(filing: Filing) -> None:
        calls.append(("insert", filing.case_number))

    async def update_classification(case_number: str, outcome) -> None:
        calls.append(("classification", case_number, outcome.lead_bucket))

    async def update_enrichment(contact: EnrichedContact) -> None:
        calls.append(("enrichment_saved", contact.track, contact.dnc_status))

    async def update_ghl_id(case_number: str, ghl_contact_id: str, track: str = "ec") -> None:
        calls.append(("ghl_id", case_number, track, ghl_contact_id))

    async def write_run_metrics(metrics: dict) -> None:
        calls.append(("metrics", metrics["filings_received"], metrics["ghl_created"]))

    async def send_run_summary(metrics: dict, *, auto_bland_enabled: bool) -> bool:
        calls.append(("summary", metrics["filings_received"], auto_bland_enabled))
        return True

    async def send_job_error(*args, **kwargs) -> bool:
        raise AssertionError(f"Unexpected job error notification: {kwargs}")

    async def normalize_address(address: str) -> str | None:
        return None

    async def lookup_property_info(filing: Filing):
        raise AssertionError("property_type_hint should avoid property lookup")

    monkeypatch.setattr(runner.rent_estimate_service, "is_enabled", lambda: False)
    monkeypatch.setattr(runner.dedup_service, "is_duplicate", is_duplicate)
    monkeypatch.setattr(runner.dedup_service, "insert_filing", insert_filing)
    monkeypatch.setattr(runner.dedup_service, "update_classification", update_classification)
    monkeypatch.setattr(runner.dedup_service, "update_enrichment", update_enrichment)
    monkeypatch.setattr(runner.dedup_service, "update_ghl_id", update_ghl_id)
    monkeypatch.setattr(runner.dedup_service, "write_run_metrics", write_run_metrics)
    monkeypatch.setattr(runner.notification_service, "send_run_summary", send_run_summary)
    monkeypatch.setattr(runner.notification_service, "send_job_error", send_job_error)
    monkeypatch.setattr(runner.geocode_service, "normalize_address", normalize_address)
    monkeypatch.setattr(runner.batchdata_service, "lookup_property_info", lookup_property_info)
