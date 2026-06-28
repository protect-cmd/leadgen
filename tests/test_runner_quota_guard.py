"""Phase 5: the pre-enrichment quota guard in pipeline.runner._enrich_one.

Verifies (guard ON) that:
  * a denied reservation HOLDS the lead and spends nothing (no enrichment call);
  * a granted reservation + real attempt COMMITS the slot;
  * a granted reservation + depletion/non-attempt ROLLS BACK the slot (not burned).
The guard-OFF path is covered by the existing 73 runner tests staying green.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import date
from unittest.mock import AsyncMock

import pytest

import pipeline.runner as runner
from models.contact import EnrichedContact
from models.filing import Filing
from services import quota_service
from services.quota_service import ReserveResult


def _filing() -> Filing:
    return Filing(
        case_number="2026-QG-1",
        tenant_name="JOHN SMITH",
        property_address="123 Main St, Houston TX 77002",
        landlord_name="ACME PROPERTIES",
        filing_date=date.today(),
        court_date=None,
        state="TX",
        county="Harris",
        notice_type="Eviction",
        source_url="http://x",
    )


def _patch_common(monkeypatch, *, contact):
    """Patch everything _enrich_one touches except the quota calls."""
    monkeypatch.setattr(runner, "_QUOTA_GUARD_ENABLED", True)
    monkeypatch.setattr(runner, "_apply_rent_precheck", AsyncMock(return_value=False))
    monkeypatch.setattr(runner, "_classify_and_store", AsyncMock(return_value="residential_approved"))
    monkeypatch.setattr(runner.dedup_service, "has_ng_phone", AsyncMock(return_value=False))
    monkeypatch.setattr(runner.dedup_service, "update_enrichment", AsyncMock(return_value=None))
    enrich = AsyncMock(return_value=contact)
    monkeypatch.setattr(runner.batchdata_service, "enrich_tenant", enrich)
    return enrich


async def _run(monkeypatch):
    return await runner._enrich_one(
        _filing(),
        defaultdict(int),
        today=date.today(),
        seen_queries=set(),
        state="TX",
        county="Harris",
        tenant_track_enabled=True,
        landlord_track_enabled=False,
    )


def test_denied_reservation_holds_lead_and_spends_nothing(monkeypatch):
    enrich = _patch_common(monkeypatch, contact=None)
    reserve = AsyncMock(return_value=ReserveResult(granted=False, used=100, remaining=0))
    commit = AsyncMock(); rollback = AsyncMock()
    monkeypatch.setattr(quota_service, "try_reserve", reserve)
    monkeypatch.setattr(quota_service, "commit", commit)
    monkeypatch.setattr(quota_service, "rollback", rollback)

    m = defaultdict(int)
    result = asyncio.run(runner._enrich_one(
        _filing(), m, today=date.today(), seen_queries=set(), state="TX",
        county="Harris", tenant_track_enabled=True, landlord_track_enabled=False,
    ))

    assert result is None                      # lead held
    enrich.assert_not_awaited()                # NOTHING spent
    commit.assert_not_awaited()
    rollback.assert_not_awaited()              # never reserved, nothing to free
    assert m["quota_held_searchbug"] == 1


def test_granted_real_attempt_commits(monkeypatch):
    contact = EnrichedContact(filing=_filing(), track="ng", phone="555-0001", searchbug_status="hit")
    _patch_common(monkeypatch, contact=contact)
    monkeypatch.setattr(quota_service, "try_reserve",
                        AsyncMock(return_value=ReserveResult(True, 1, 99)))
    commit = AsyncMock(); rollback = AsyncMock()
    monkeypatch.setattr(quota_service, "commit", commit)
    monkeypatch.setattr(quota_service, "rollback", rollback)

    result = asyncio.run(_run(monkeypatch))

    assert result is not None
    commit.assert_awaited_once()               # real attempt -> consume the slot
    rollback.assert_not_awaited()


def test_granted_depletion_nonattempt_rolls_back(monkeypatch):
    # phone-less + "(none)" status == credit-depletion non-attempt (not a real hit)
    contact = EnrichedContact(filing=_filing(), track="ng", phone=None, searchbug_status="(none)")
    _patch_common(monkeypatch, contact=contact)
    monkeypatch.setattr(quota_service, "try_reserve",
                        AsyncMock(return_value=ReserveResult(True, 1, 99)))
    commit = AsyncMock(); rollback = AsyncMock()
    monkeypatch.setattr(quota_service, "commit", commit)
    monkeypatch.setattr(quota_service, "rollback", rollback)

    asyncio.run(_run(monkeypatch))

    rollback.assert_awaited_once()             # non-attempt -> free the slot, not burned
    commit.assert_not_awaited()
