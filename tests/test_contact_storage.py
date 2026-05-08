from __future__ import annotations

import asyncio
from datetime import date

from models.contact import EnrichedContact
from models.filing import Filing
from services import dedup_service


class FakeSupabaseQuery:
    def __init__(self, client: "FakeSupabaseClient", table_name: str):
        self.client = client
        self.table_name = table_name
        self.operation = ""
        self.payload = None
        self.kwargs = {}
        self.filters: list[tuple[str, str, object]] = []

    def upsert(self, payload, **kwargs):
        self.operation = "upsert"
        self.payload = payload
        self.kwargs = kwargs
        return self

    def update(self, payload):
        self.operation = "update"
        self.payload = payload
        return self

    def eq(self, column: str, value):
        self.filters.append(("eq", column, value))
        return self

    def execute(self):
        self.client.calls.append(
            {
                "table": self.table_name,
                "operation": self.operation,
                "payload": self.payload,
                "kwargs": self.kwargs,
                "filters": self.filters,
            }
        )
        return type("Response", (), {"data": []})()


class FakeSupabaseClient:
    def __init__(self):
        self.calls: list[dict] = []

    def table(self, table_name: str):
        return FakeSupabaseQuery(self, table_name)


def _contact(track: str, phone: str | None) -> EnrichedContact:
    filing = Filing(
        case_number=f"TEST-CONTACT-{track.upper()}",
        tenant_name="Maria Garcia",
        property_address="123 Main St, Houston, TX 77002",
        landlord_name="Grant Owner LLC",
        filing_date=date(2026, 5, 8),
        state="TX",
        county="Harris",
        notice_type="Eviction",
        source_url="https://example.test",
    )
    return EnrichedContact(
        filing=filing,
        track=track,
        phone=phone,
        email=f"{track}@example.test" if phone else None,
        property_type="residential",
        estimated_rent=1800,
        dnc_status="clear",
        dnc_source="batchdata:test",
        language_hint="spanish_likely" if track == "ng" else None,
    )


def test_ng_enrichment_writes_tenant_contact_without_overwriting_shared_filing_contact(monkeypatch):
    fake = FakeSupabaseClient()
    monkeypatch.setattr(dedup_service, "_client", fake)

    asyncio.run(dedup_service.upsert_contact_enrichment(_contact("ng", "+12135550101")))

    lead_call = next(call for call in fake.calls if call["table"] == "lead_contacts")
    assert lead_call["operation"] == "upsert"
    assert lead_call["kwargs"] == {"on_conflict": "case_number,track"}
    assert lead_call["payload"]["track"] == "ng"
    assert lead_call["payload"]["contact_name"] == "Maria Garcia"
    assert lead_call["payload"]["phone"] == "+12135550101"

    filing_call = next(call for call in fake.calls if call["table"] == "filings")
    assert "phone" not in filing_call["payload"]
    assert "email" not in filing_call["payload"]
    assert filing_call["payload"]["ng_dnc_status"] == "clear"
    assert filing_call["payload"]["language_hint"] == "spanish_likely"


def test_ec_enrichment_keeps_legacy_filing_contact_for_grant_dashboard(monkeypatch):
    fake = FakeSupabaseClient()
    monkeypatch.setattr(dedup_service, "_client", fake)

    asyncio.run(dedup_service.upsert_contact_enrichment(_contact("ec", "+12135550100")))

    lead_call = next(call for call in fake.calls if call["table"] == "lead_contacts")
    assert lead_call["payload"]["track"] == "ec"
    assert lead_call["payload"]["contact_name"] == "Grant Owner LLC"

    filing_call = next(call for call in fake.calls if call["table"] == "filings")
    assert filing_call["payload"]["phone"] == "+12135550100"
    assert filing_call["payload"]["email"] == "ec@example.test"
    assert filing_call["payload"]["dnc_status"] == "clear"


def test_status_updates_write_track_specific_contact_and_legacy_columns(monkeypatch):
    fake = FakeSupabaseClient()
    monkeypatch.setattr(dedup_service, "_client", fake)

    asyncio.run(dedup_service.update_contact_ghl_id("CASE-1", "ghl-ng", "ng"))
    asyncio.run(dedup_service.set_bland_status("CASE-1", "ng", "pending_dnc_review"))

    lead_updates = [
        call for call in fake.calls
        if call["table"] == "lead_contacts" and call["operation"] == "update"
    ]
    assert lead_updates[0]["payload"] == {"ghl_contact_id": "ghl-ng"}
    assert ("eq", "track", "ng") in lead_updates[0]["filters"]
    assert lead_updates[1]["payload"] == {"bland_status": "pending_dnc_review"}
    assert ("eq", "track", "ng") in lead_updates[1]["filters"]

    filing_updates = [
        call for call in fake.calls
        if call["table"] == "filings" and call["operation"] == "update"
    ]
    assert filing_updates[0]["payload"] == {"ng_ghl_contact_id": "ghl-ng"}
    assert filing_updates[1]["payload"] == {"ng_bland_status": "pending_dnc_review"}


def test_vantage_dashboard_overlay_clears_shared_contact_when_no_ng_contact_exists():
    rows = dedup_service._overlay_contact_rows(
        [
            {
                "case_number": "CASE-1",
                "phone": "+12135550100",
                "email": "grant@example.test",
                "dnc_status": "clear",
                "dnc_source": "grant-source",
            }
        ],
        [],
        clear_missing_contact=True,
    )

    assert rows[0]["phone"] is None
    assert rows[0]["email"] is None
    assert rows[0]["dnc_status"] == "unknown"
    assert rows[0]["dnc_source"] is None
