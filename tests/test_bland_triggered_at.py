import pytest
from unittest.mock import MagicMock

from services import dedup_service


def _capture_client():
    """MagicMock client that records every .update(payload) call."""
    payloads: list[dict] = []
    client = MagicMock()

    def _table(name):
        t = MagicMock()

        def _update(payload):
            payloads.append(payload)
            return MagicMock()  # .eq().eq().execute() chain → MagicMock, harmless

        t.update.side_effect = _update
        return t

    client.table.side_effect = _table
    return client, payloads


def _lead_payload(payloads):
    # the lead_contacts write is the one keyed on "bland_status" (filings uses ng_bland_status)
    return next(p for p in payloads if "bland_status" in p)


@pytest.mark.asyncio
async def test_stamps_bland_triggered_at_when_column_known(monkeypatch):
    client, payloads = _capture_client()
    monkeypatch.setattr(dedup_service, "_client", client)
    monkeypatch.setattr(dedup_service, "_lead_contact_known_columns",
                        lambda: {"bland_status", "bland_call_id", "bland_triggered_at"})

    await dedup_service.set_bland_status("CN1", "ng", "triggered", call_id="call-1")

    lp = _lead_payload(payloads)
    assert lp["bland_call_id"] == "call-1"
    assert lp.get("bland_triggered_at")


@pytest.mark.asyncio
async def test_omits_bland_triggered_at_when_column_unknown(monkeypatch):
    client, payloads = _capture_client()
    monkeypatch.setattr(dedup_service, "_client", client)
    monkeypatch.setattr(dedup_service, "_lead_contact_known_columns",
                        lambda: {"bland_status", "bland_call_id"})  # column not present

    await dedup_service.set_bland_status("CN1", "ng", "triggered", call_id="call-1")

    lp = _lead_payload(payloads)
    assert lp["bland_call_id"] == "call-1"          # still written
    assert "bland_triggered_at" not in lp            # not sent — optional write stays safe
