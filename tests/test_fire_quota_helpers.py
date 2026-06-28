"""Phase 5: per-business Bland cap helpers in fire_service.

Guard OFF must behave exactly like the legacy global SQLite cap; guard ON must
route through the per-business quota_service (reserve/commit/rollback).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import services.fire_service as fs
from pipeline.contract import Business
from services import quota_service
from services.quota_service import ReserveResult


# --- guard OFF: legacy global cap, unchanged -------------------------------
def test_reserve_guard_off_uses_global_cap(monkeypatch):
    monkeypatch.setattr(fs, "_QUOTA_GUARD_ENABLED", False)
    monkeypatch.setattr(fs, "_bland_cap_ok", MagicMock(return_value=True))
    assert asyncio.run(fs._bland_reserve(Business.VANTAGE, "x")) is True
    fs._bland_cap_ok.assert_called_once()


def test_settle_guard_off_increments_only_on_success(monkeypatch):
    monkeypatch.setattr(fs, "_QUOTA_GUARD_ENABLED", False)
    inc = MagicMock()
    monkeypatch.setattr(fs, "_bland_cap_increment", inc)
    asyncio.run(fs._bland_settle(Business.VANTAGE, "x", success=True))
    inc.assert_called_once()
    inc.reset_mock()
    asyncio.run(fs._bland_settle(Business.VANTAGE, "x", success=False))
    inc.assert_not_called()


# --- guard ON: per-business quota_service ----------------------------------
def test_reserve_guard_on_uses_quota(monkeypatch):
    monkeypatch.setattr(fs, "_QUOTA_GUARD_ENABLED", True)
    reserve = AsyncMock(return_value=ReserveResult(granted=False, used=100, remaining=0))
    monkeypatch.setattr(quota_service, "try_reserve", reserve)
    assert asyncio.run(fs._bland_reserve(Business.ISTS, "c1")) is False
    args = reserve.await_args
    assert args.args[0] is Business.ISTS and args.args[1] == "bland" and args.args[2] == "c1"


def test_settle_guard_on_commits_on_success(monkeypatch):
    monkeypatch.setattr(fs, "_QUOTA_GUARD_ENABLED", True)
    monkeypatch.setattr(fs, "_bland_cap_increment", MagicMock())
    commit = AsyncMock(); rollback = AsyncMock()
    monkeypatch.setattr(quota_service, "commit", commit)
    monkeypatch.setattr(quota_service, "rollback", rollback)
    asyncio.run(fs._bland_settle(Business.VANTAGE, "c2", success=True))
    commit.assert_awaited_once()
    rollback.assert_not_awaited()
    fs._bland_cap_increment.assert_called_once()  # display counter still bumped


def test_settle_guard_on_rolls_back_on_failure(monkeypatch):
    monkeypatch.setattr(fs, "_QUOTA_GUARD_ENABLED", True)
    monkeypatch.setattr(fs, "_bland_cap_increment", MagicMock())
    commit = AsyncMock(); rollback = AsyncMock()
    monkeypatch.setattr(quota_service, "commit", commit)
    monkeypatch.setattr(quota_service, "rollback", rollback)
    asyncio.run(fs._bland_settle(Business.ISTS, "c3", success=False))
    rollback.assert_awaited_once()
    commit.assert_not_awaited()
    fs._bland_cap_increment.assert_not_called()  # no dial happened
