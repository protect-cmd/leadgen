"""9-gate enrichment filter — pure unit tests for pipeline/gates.py."""
from __future__ import annotations
from datetime import date

from pipeline.gates import (
    gate_filing_window, gate_court_date, gate_address,
    gate_name, gate_query_dedup,
)


def test_filing_window_passes_recent():
    assert gate_filing_window(date(2026, 5, 25), today=date(2026, 5, 28), window_days=10) is True


def test_filing_window_fails_old():
    assert gate_filing_window(date(2026, 5, 1), today=date(2026, 5, 28), window_days=10) is False


def test_filing_window_zero_age_passes():
    # filing today, today=today → 0 days elapsed, passes.
    assert gate_filing_window(date(2026, 5, 28), today=date(2026, 5, 28), window_days=10) is True


def test_court_date_none_passes():
    assert gate_court_date(None, today=date(2026, 5, 28)) is True


def test_court_date_future_passes():
    assert gate_court_date(date(2026, 6, 1), today=date(2026, 5, 28)) is True


def test_court_date_today_passes():
    assert gate_court_date(date(2026, 5, 28), today=date(2026, 5, 28)) is True


def test_court_date_past_fails():
    assert gate_court_date(date(2026, 5, 20), today=date(2026, 5, 28)) is False


def test_address_with_street_number_and_zip_passes():
    assert gate_address("123 Main St, Houston, TX 77002") is True


def test_address_without_street_number_fails():
    assert gate_address("Main St, Houston, TX 77002") is False


def test_address_without_state_zip_fails():
    assert gate_address("123 Main St") is False


def test_address_blank_fails():
    assert gate_address("") is False


def test_name_clean_parsing_passes():
    assert gate_name("Maria Garcia") is True


def test_name_placeholder_fails():
    assert gate_name("John Doe") is False


def test_name_entity_fails():
    assert gate_name("Pure Auto Spa, LLC") is False


def test_name_with_occupant_token_fails():
    # "Zehneel Occupants" — bad-token rule rejects.
    assert gate_name("Zehneel Occupants") is False


def test_name_with_et_al_fails():
    # Clean tenant name strips "et al"; what remains may still parse, but the
    # raw input contains a bad-token signal. The cleaner removes the trailer
    # first, leaving "John Smith" which is fine. Verify the trailer-cleaner
    # path is what saves us, not the bad-token check.
    assert gate_name("John Smith, et al.") is True


def test_query_dedup_first_pass_second_fail():
    seen: set[str] = set()
    assert gate_query_dedup("maria", "garcia", "123 Main St", "77002", seen) is True
    assert gate_query_dedup("maria", "garcia", "123 Main St", "77002", seen) is False


def test_query_dedup_case_insensitive():
    seen: set[str] = set()
    assert gate_query_dedup("Maria", "Garcia", "123 Main St", "77002", seen) is True
    assert gate_query_dedup("maria", "garcia", "123 main st", "77002", seen) is False


# ----- Integration: gates wired into runner.run -----

import pytest
from unittest.mock import AsyncMock, patch
from models.filing import Filing
from models.contact import EnrichedContact
from pipeline import runner


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("TENANT_TRACK_ENABLED", "true")
    monkeypatch.setenv("LANDLORD_TRACK_ENABLED", "false")
    monkeypatch.setattr(runner, "_CAPTURE_EXPANDED_ZIPS", False)


def _filing(**overrides) -> Filing:
    today = date.today()
    defaults = dict(
        case_number="G1", tenant_name="Maria Garcia",
        property_address="123 Main St, Houston, TX 77002",
        landlord_name="ACME", filing_date=today,
        state="TX", county="Harris",
        notice_type="Nonpayment - Residential", source_url="x",
    )
    defaults.update(overrides)
    return Filing(**defaults)


def _ng_return(filing: Filing) -> EnrichedContact:
    return EnrichedContact(
        filing=filing, track="ng", phone=None, email=None,
        property_type="residential",
    )


@pytest.mark.asyncio
async def test_runner_skips_overdue_filing():
    today = date.today()
    yesterday = date.fromordinal(today.toordinal() - 1)
    filing = _filing(court_date=yesterday)  # overdue

    mock_tenant = AsyncMock(return_value=_ng_return(filing))
    with patch("services.batchdata_service.enrich_tenant", new=mock_tenant), \
         patch("services.dedup_service.is_duplicate", new=AsyncMock(return_value=False)), \
         patch("services.dedup_service.insert_filing", new=AsyncMock()), \
         patch("services.dedup_service.update_classification", new=AsyncMock()), \
         patch("services.dedup_service.update_enrichment", new=AsyncMock()), \
         patch("services.geocode_service.normalize_address", new=AsyncMock(return_value=None)), \
         patch("services.notification_service.send_run_summary", new=AsyncMock()), \
         patch("services.dedup_service.write_run_metrics", new=AsyncMock()):

        await runner.run([filing], state="TX", county="Harris")

    mock_tenant.assert_not_called()


@pytest.mark.asyncio
async def test_runner_skips_bad_name():
    filing = _filing(tenant_name="John Doe")  # placeholder

    mock_tenant = AsyncMock(return_value=_ng_return(filing))
    with patch("services.batchdata_service.enrich_tenant", new=mock_tenant), \
         patch("services.dedup_service.is_duplicate", new=AsyncMock(return_value=False)), \
         patch("services.dedup_service.insert_filing", new=AsyncMock()), \
         patch("services.dedup_service.update_classification", new=AsyncMock()), \
         patch("services.dedup_service.update_enrichment", new=AsyncMock()), \
         patch("services.geocode_service.normalize_address", new=AsyncMock(return_value=None)), \
         patch("services.notification_service.send_run_summary", new=AsyncMock()), \
         patch("services.dedup_service.write_run_metrics", new=AsyncMock()):

        await runner.run([filing], state="TX", county="Harris")

    mock_tenant.assert_not_called()


@pytest.mark.asyncio
async def test_runner_skips_duplicate_query_in_run():
    a = _filing(case_number="DUP1", tenant_name="Maria Garcia",
                property_address="123 Main St, Houston, TX 77002")
    b = _filing(case_number="DUP2", tenant_name="Maria Garcia",
                property_address="123 Main St, Houston, TX 77002")

    def _ret(filing, **_):
        return _ng_return(filing)

    mock_tenant = AsyncMock(side_effect=_ret)
    with patch("services.batchdata_service.enrich_tenant", new=mock_tenant), \
         patch("services.dedup_service.is_duplicate", new=AsyncMock(return_value=False)), \
         patch("services.dedup_service.insert_filing", new=AsyncMock()), \
         patch("services.dedup_service.update_classification", new=AsyncMock()), \
         patch("services.dedup_service.update_enrichment", new=AsyncMock()), \
         patch("services.geocode_service.normalize_address", new=AsyncMock(return_value=None)), \
         patch("services.notification_service.send_run_summary", new=AsyncMock()), \
         patch("services.dedup_service.write_run_metrics", new=AsyncMock()):

        await runner.run([a, b], state="TX", county="Harris")

    # First filing reaches enrichment; second is dropped by gate_query_dedup.
    assert mock_tenant.call_count == 1
