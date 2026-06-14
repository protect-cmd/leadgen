import pytest

import scripts.push_franklin_filings as pf


@pytest.mark.asyncio
async def test_emit_run_metrics_writes_franklin_row(monkeypatch):
    captured: dict = {}

    async def fake_write(metrics):
        captured.update(metrics)

    monkeypatch.setattr("services.dedup_service.write_run_metrics", fake_write)

    await pf._emit_run_metrics(pf.PushSummary(received=69, inserted=60, duplicates=9))

    assert captured["state"] == "OH"
    assert captured["county"] == "Franklin"
    assert captured["filings_received"] == 69
    assert captured["duplicates_skipped"] == 9
    assert "run_at" in captured
