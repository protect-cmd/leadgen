import pytest

from models.contact import EnrichedContact
from tests.test_runner_tracks import _filing


@pytest.mark.asyncio
async def test_trigger_voicemail_rejects_dnc_before_dispatch(monkeypatch):
    from services import bland_service

    contact = EnrichedContact(
        filing=_filing(),
        track="ng",
        phone="6152222222",
        property_type="residential",
        estimated_rent=1800,
    )

    monkeypatch.setattr("services.dnc_service.verdict", lambda phone: "dnc")

    with pytest.raises(RuntimeError, match="DNC"):
        await bland_service.trigger_voicemail(contact)


@pytest.mark.asyncio
async def test_trigger_voicemail_blocks_outside_calling_window(monkeypatch):
    from services import bland_service
    from services.call_window import OutsideCallWindow

    contact = EnrichedContact(
        filing=_filing(),
        track="ng",
        phone="6152222222",
        property_type="residential",
        estimated_rent=1800,
    )

    monkeypatch.setattr("services.dnc_service.verdict", lambda phone: "callable")
    monkeypatch.setattr("services.call_window.in_call_window", lambda state, now_utc=None: False)

    with pytest.raises(OutsideCallWindow):
        await bland_service.trigger_voicemail(contact)
