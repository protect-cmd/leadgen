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


# ── _get_ng_dashboard_leads behavior ─────────────────────────────────────────

from unittest.mock import MagicMock, patch


def _fake_supabase_pair(ng_contact_rows: list[dict], filing_rows: list[dict]):
    """Build a MagicMock _client that returns the given contact/filing data."""
    client = MagicMock()

    contact_table = MagicMock()
    contact_table.select.return_value = contact_table
    contact_table.eq.return_value = contact_table
    contact_table.execute.return_value = MagicMock(data=ng_contact_rows)

    filing_table = MagicMock()
    # All chainable filings methods (select/eq/or_/in_/order/limit) return self;
    # only execute() returns data.
    for method in ("select", "eq", "or_", "in_", "order", "limit"):
        getattr(filing_table, method).return_value = filing_table
    filing_table.execute.return_value = MagicMock(data=filing_rows)

    def _table(name: str):
        return contact_table if name == "lead_contacts" else filing_table

    client.table.side_effect = _table
    return client


def test_ng_residential_returns_only_actionable():
    ng_contacts = [
        {"case_number": "A1", "track": "ng", "phone": "+15551110001",
         "bland_status": "pending", "dnc_status": "clear",
         "ghl_contact_id": "ghl-A1"},
        {"case_number": "A2", "track": "ng", "phone": "+15551110002",
         "bland_status": "triggered", "dnc_status": "clear",
         "ghl_contact_id": "ghl-A2"},
        {"case_number": "A3", "track": "ng", "phone": None,
         "bland_status": None, "dnc_status": "unknown",
         "ghl_contact_id": None},
        {"case_number": "A4", "track": "ng", "phone": "+15551110004",
         "bland_status": "pending_dnc_review", "dnc_status": "unknown",
         "ghl_contact_id": None},
    ]
    filings = [
        {"case_number": "A1", "tenant_name": "T1", "lead_bucket": "residential_approved"},
        {"case_number": "A4", "tenant_name": "T4", "lead_bucket": "residential_approved"},
    ]
    fake_client = _fake_supabase_pair(ng_contacts, filings)

    with patch.object(dedup_service, "_client", fake_client):
        rows = dedup_service._get_ng_dashboard_leads("ng_residential", 100)

    case_numbers = {r["case_number"] for r in rows}
    assert case_numbers == {"A1", "A4"}, (
        f"Expected actionable cases A1+A4, got {case_numbers}"
    )


def test_ng_already_called_returns_only_worked():
    ng_contacts = [
        {"case_number": "B1", "track": "ng", "phone": "+15552220001",
         "bland_status": "triggered", "dnc_status": "clear",
         "ghl_contact_id": "ghl-B1"},
        {"case_number": "B2", "track": "ng", "phone": "+15552220002",
         "bland_status": "pending", "dnc_status": "clear",
         "ghl_contact_id": "ghl-B2"},
        {"case_number": "B3", "track": "ng", "phone": "+15552220003",
         "bland_status": "wrong_brand_review", "dnc_status": "clear",
         "ghl_contact_id": "ghl-B3"},
    ]
    filings = [
        {"case_number": "B1", "tenant_name": "T1", "lead_bucket": "residential_approved"},
        {"case_number": "B3", "tenant_name": "T3", "lead_bucket": "residential_approved"},
    ]
    fake_client = _fake_supabase_pair(ng_contacts, filings)

    with patch.object(dedup_service, "_client", fake_client):
        rows = dedup_service._get_ng_dashboard_leads("ng_already_called", 100)

    case_numbers = {r["case_number"] for r in rows}
    assert case_numbers == {"B1", "B3"}


def test_ng_held_view_unchanged_by_actionable_filter():
    """Held / commercial / discarded views must keep their existing behavior."""
    ng_contacts = [
        {"case_number": "C1", "track": "ng", "phone": None,
         "bland_status": "missing_contact_data", "dnc_status": "unknown",
         "ghl_contact_id": None},
    ]
    filings = [
        {"case_number": "C1", "tenant_name": "T1", "lead_bucket": "held"},
    ]
    fake_client = _fake_supabase_pair(ng_contacts, filings)

    with patch.object(dedup_service, "_client", fake_client):
        rows = dedup_service._get_ng_dashboard_leads("ng_held", 100)

    # Held leads with no phone still appear — operator reviews them
    assert {r["case_number"] for r in rows} == {"C1"}
