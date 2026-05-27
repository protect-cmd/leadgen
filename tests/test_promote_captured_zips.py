"""Unit tests for scripts/promote_captured_zips with mocked Supabase client."""
from __future__ import annotations
from unittest.mock import MagicMock
from scripts import promote_captured_zips as mod


def _row(case_number, zip_, bucket="captured"):
    return {
        "case_number": case_number,
        "property_zip": zip_,
        "lead_bucket": bucket,
        "qualification_notes": "Captured: ZIP off legacy allowlist...",
    }


def _make_client(rows: list[dict]) -> MagicMock:
    client = MagicMock()
    chain = (
        client.table.return_value
        .select.return_value
        .eq.return_value
        .in_.return_value
        .gte.return_value
        .execute.return_value
    )
    chain.data = rows
    return client


def test_dry_run_does_not_write(monkeypatch):
    rows = [_row("A", "77090"), _row("B", "77090")]
    client = _make_client(rows)
    monkeypatch.setattr(mod, "_client", client)

    result = mod.run(state="TX", zips=["77090"], since="2026-05-01",
                     dry_run=True, demote=False)

    assert result["projected_promotions"] == 2
    # No update call was issued.
    client.table.return_value.update.assert_not_called()


def test_promotion_updates_bucket(monkeypatch):
    rows = [_row("A", "77090")]
    client = _make_client(rows)
    monkeypatch.setattr(mod, "_client", client)

    result = mod.run(state="TX", zips=["77090"], since="2026-05-01",
                     dry_run=False, demote=False)

    assert result["promoted"] == 1
    update_calls = client.table.return_value.update.call_args_list
    assert any("residential_approved" in str(c) for c in update_calls)


def test_demote_reverses_bucket(monkeypatch):
    rows = [_row("A", "77090", bucket="residential_approved")]
    client = _make_client(rows)
    monkeypatch.setattr(mod, "_client", client)

    result = mod.run(state="TX", zips=["77090"], since="2026-05-01",
                     dry_run=False, demote=True)

    assert result["promoted"] == 1
    update_calls = client.table.return_value.update.call_args_list
    # Demoted record had its bucket flipped back to captured.
    assert any("captured" in str(c) for c in update_calls)


def test_only_source_bucket_rows_eligible(monkeypatch):
    # Mix of buckets returned; only captured rows should be eligible for promotion.
    rows = [
        _row("A", "77090", bucket="captured"),
        _row("B", "77090", bucket="residential_approved"),
        _row("C", "77090", bucket="discarded"),
    ]
    client = _make_client(rows)
    monkeypatch.setattr(mod, "_client", client)

    result = mod.run(state="TX", zips=["77090"], since="2026-05-01",
                     dry_run=True, demote=False)

    assert result["projected_promotions"] == 1
