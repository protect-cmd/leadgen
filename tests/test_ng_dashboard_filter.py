"""Tests for the Vantage (NG) dashboard actionable filter and Already Called tab.

The main "Vantage Residential" view should only show leads the operator can
act on today — phone present, not yet dialed by Bland, not in compliance hold.
The new "Vantage Already Called" view shows leads where Bland already ran.
"""
from __future__ import annotations

from services import dedup_service


# ── predicate: _is_ng_contact_actionable ─────────────────────────────────────

def test_actionable_requires_phone():
    assert dedup_service._is_ng_contact_actionable(
        {"phone": "+15551112222", "bland_status": "pending"}
    ) is True
    assert dedup_service._is_ng_contact_actionable(
        {"phone": None, "bland_status": "pending"}
    ) is False
    assert dedup_service._is_ng_contact_actionable(
        {"phone": "", "bland_status": "pending"}
    ) is False


def test_actionable_excludes_worked_bland_statuses():
    for worked in ("triggered", "wrong_brand_review", "missing_contact_data", "blocked_dnc"):
        assert dedup_service._is_ng_contact_actionable(
            {"phone": "+15551112222", "bland_status": worked}
        ) is False, f"{worked} should be excluded"


def test_actionable_includes_visible_bland_statuses():
    for visible in ("pending", "pending_dnc_review", "skipped", None):
        assert dedup_service._is_ng_contact_actionable(
            {"phone": "+15551112222", "bland_status": visible}
        ) is True, f"{visible!r} should be visible"


def test_actionable_includes_both_dnc_clear_and_unknown():
    # Caller decides — both visible per operator preference
    for status in ("clear", "unknown"):
        assert dedup_service._is_ng_contact_actionable(
            {"phone": "+15551112222", "bland_status": "pending", "dnc_status": status}
        ) is True


# ── predicate: _is_ng_contact_already_called ────────────────────────────────

def test_already_called_matches_triggered_and_wrong_brand():
    assert dedup_service._is_ng_contact_already_called(
        {"bland_status": "triggered"}
    ) is True
    assert dedup_service._is_ng_contact_already_called(
        {"bland_status": "wrong_brand_review"}
    ) is True


def test_already_called_excludes_other_statuses():
    for not_called in (
        "pending", "pending_dnc_review", "skipped",
        "missing_contact_data", "blocked_dnc", None,
    ):
        assert dedup_service._is_ng_contact_already_called(
            {"bland_status": not_called}
        ) is False, f"{not_called!r} should not count as already-called"
