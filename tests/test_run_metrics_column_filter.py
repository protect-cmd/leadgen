"""Defensive write_run_metrics: drop fields whose columns don't exist yet.

When new metric fields land in code before the Supabase migration runs, we
don't want the entire run summary write to fail with PGRST204. Instead, filter
the payload to known columns and log a warning so the gap is visible.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from services import dedup_service


@pytest.fixture(autouse=True)
def reset_columns_cache():
    dedup_service._reset_run_metrics_columns_cache_for_tests()
    yield
    dedup_service._reset_run_metrics_columns_cache_for_tests()


def _mock_client_with_columns(columns: set[str]):
    """Construct a chainable mock that mimics the supabase client just enough
    to feed _run_metrics_known_columns(). Returns (mock_client, captured_inserts)."""
    captured = []
    sample_row = {col: 0 for col in columns}
    sample_row.setdefault("run_at", "2026-05-22T00:00:00+00:00")

    table = MagicMock()
    # discovery: .select("*").limit(1) returns existing columns
    select_chain = MagicMock()
    select_chain.limit.return_value.execute.return_value = MagicMock(data=[sample_row])
    table.select.return_value = select_chain
    # insert: capture payload
    insert_chain = MagicMock()
    def insert_side_effect(payload):
        captured.append(payload)
        return insert_chain
    table.insert.side_effect = insert_side_effect
    insert_chain.execute.return_value = MagicMock(data=[])

    client = MagicMock()
    client.table.return_value = table
    return client, captured


@pytest.mark.asyncio
async def test_write_run_metrics_drops_unknown_columns():
    client, captured = _mock_client_with_columns({
        "run_at", "state", "county", "filings_received", "phones_found",
        "batchdata_calls", "ghl_created", "elapsed_seconds",
    })

    metrics = {
        "run_at": "2026-05-22T00:00:00+00:00",
        "state": "OH",
        "county": "Hamilton",
        "filings_received": 100,
        "phones_found": 7,
        "batchdata_calls": 25,
        "ghl_created": 5,
        "elapsed_seconds": 60.5,
        # These four don't exist in the schema yet:
        "ftc_scrubs_upgraded": 3,
        "ng_phones_pushed": 4,
        "searchbug_calls": 12,
        "searchbug_daily_total": 65,
    }

    with patch.object(dedup_service, "_client", client):
        await dedup_service.write_run_metrics(metrics)

    assert len(captured) == 1
    payload = captured[0]
    # Known fields preserved
    assert payload["filings_received"] == 100
    assert payload["phones_found"] == 7
    assert payload["ghl_created"] == 5
    # Unknown fields dropped
    assert "ftc_scrubs_upgraded" not in payload
    assert "ng_phones_pushed" not in payload
    assert "searchbug_calls" not in payload
    assert "searchbug_daily_total" not in payload


@pytest.mark.asyncio
async def test_write_run_metrics_includes_columns_after_migration():
    # All four new columns now exist
    client, captured = _mock_client_with_columns({
        "run_at", "state", "county", "filings_received", "phones_found",
        "batchdata_calls", "ghl_created", "elapsed_seconds",
        "ftc_scrubs_upgraded", "ng_phones_pushed",
        "searchbug_calls", "searchbug_daily_total",
    })

    metrics = {
        "run_at": "2026-05-22T00:00:00+00:00",
        "state": "OH",
        "county": "Hamilton",
        "filings_received": 100,
        "phones_found": 7,
        "batchdata_calls": 25,
        "ghl_created": 5,
        "elapsed_seconds": 60.5,
        "ftc_scrubs_upgraded": 3,
        "ng_phones_pushed": 4,
        "searchbug_calls": 12,
        "searchbug_daily_total": 65,
    }

    with patch.object(dedup_service, "_client", client):
        await dedup_service.write_run_metrics(metrics)

    payload = captured[0]
    assert payload["ftc_scrubs_upgraded"] == 3
    assert payload["ng_phones_pushed"] == 4
    assert payload["searchbug_calls"] == 12
    assert payload["searchbug_daily_total"] == 65
