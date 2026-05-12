from __future__ import annotations

from datetime import date

import pytest

from models.contact import EnrichedContact
from models.filing import Filing
from services import batchdata_service, bland_service, dedup_service, dnc_service


def _filing() -> Filing:
    return Filing(
        case_number="TEST-BRAND-001",
        tenant_name="Jane Tenant",
        property_address="123 Main St, Los Angeles, CA 90001",
        landlord_name="Grant Owner",
        filing_date=date(2026, 5, 6),
        state="CA",
        county="Los Angeles",
        notice_type="Unlawful Detainer",
        source_url="https://example.test",
    )


def test_bland_scripts_use_new_brand_names_only():
    ec_script = bland_service.render_voicemail_script(
        EnrichedContact(
            filing=_filing(),
            track="ec",
            phone="+12135550100",
            dnc_status="clear",
        )
    )
    vdg_script = bland_service.render_voicemail_script(
        EnrichedContact(
            filing=_filing(),
            track="ng",
            phone="+12135550101",
            dnc_status="clear",
        )
    )

    combined = f"{ec_script}\n{vdg_script}"
    assert "Grant Ellis Group" in ec_script
    assert "Vantage Defense Group" in vdg_script
    assert "EvictionCommand" not in combined
    assert "Eviction Command" not in combined
    assert "Nobles & Greyson" not in combined
    assert "Nobles and Greyson" not in combined


def test_bland_renders_spanish_vdg_script_for_spanish_likely_contacts():
    script = bland_service.render_voicemail_script(
        EnrichedContact(
            filing=Filing(
                case_number="TEST-SPANISH-SCRIPT",
                tenant_name="Maria Garcia",
                property_address="123 Main St, Houston, TX 77002",
                landlord_name="Grant Owner",
                filing_date=date(2026, 5, 6),
                state="TX",
                county="Harris",
                notice_type="Eviction",
                source_url="https://example.test",
            ),
            track="ng",
            phone="+12135550100",
            dnc_status="clear",
            language_hint="spanish_likely",
        )
    )

    assert "Hola, este mensaje es para Maria." in script
    assert "Vantage Defense Group" in script
    assert "consulta gratuita" in script


def test_bland_ec_script_uses_callback_number(monkeypatch):
    monkeypatch.setattr(bland_service, "_EC_PHONE_NUMBER", "+18185550100")
    monkeypatch.setattr(bland_service, "_EC_CALLBACK_NUMBER", "+18885550100")

    script = bland_service.render_voicemail_script(
        EnrichedContact(
            filing=_filing(),
            track="ec",
            phone="+12135550100",
            dnc_status="clear",
        )
    )

    assert "+18885550100" in script
    assert "+18185550100" not in script


def test_bland_spanish_script_uses_spanish_callback_number(monkeypatch):
    monkeypatch.setattr(bland_service, "_NG_PHONE_NUMBER", "+18185550101")
    monkeypatch.setattr(bland_service, "_NG_SPANISH_PHONE_NUMBER", "+18185550102")
    monkeypatch.setattr(bland_service, "_NG_CALLBACK_NUMBER", "+18885550101")
    monkeypatch.setattr(bland_service, "_NG_SPANISH_CALLBACK_NUMBER", "+18885550102")

    script = bland_service.render_voicemail_script(
        EnrichedContact(
            filing=Filing(
                case_number="TEST-SPANISH-CALLBACK",
                tenant_name="Maria Garcia",
                property_address="123 Main St, Houston, TX 77002",
                landlord_name="Grant Owner",
                filing_date=date(2026, 5, 6),
                state="TX",
                county="Harris",
                notice_type="Eviction",
                source_url="https://example.test",
            ),
            track="ng",
            phone="+12135550100",
            dnc_status="clear",
            language_hint="spanish_likely",
        )
    )

    assert "+18885550102" in script
    assert "+18885550101" not in script
    assert "+18185550102" not in script


@pytest.mark.asyncio
async def test_bland_request_data_uses_callback_number(monkeypatch):
    payloads: list[dict] = []

    class Response:
        status_code = 200
        text = "ok"

        def json(self):
            return {"call_id": "call-123"}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json, headers):
            payloads.append(json)
            return Response()

    monkeypatch.setenv("BLAND_API_KEY", "key")
    monkeypatch.setattr(bland_service, "_EC_AGENT_ID", "agent-ec")
    monkeypatch.setattr(bland_service, "_EC_PHONE_NUMBER", "+18185550100")
    monkeypatch.setattr(bland_service, "_EC_CALLBACK_NUMBER", "+18885550100")
    monkeypatch.setattr(bland_service.httpx, "AsyncClient", lambda **kwargs: Client())

    call_id = await bland_service.trigger_voicemail(
        EnrichedContact(
            filing=_filing(),
            track="ec",
            phone="+12135550100",
            dnc_status="clear",
        )
    )

    assert call_id == "call-123"
    assert payloads[0]["from"] == "+18185550100"
    assert payloads[0]["request_data"]["ec_phone"] == "+18885550100"
    assert "+18885550100" in payloads[0]["voicemail"]["message"]


def test_batchdata_phone_selection_preserves_clear_dnc_status():
    result = batchdata_service._best_phone_result(
        [
            {"number": "5550000001", "type": "Mobile", "score": 90, "dnc": True},
            {"number": "5550000002", "type": "Mobile", "score": 80, "dnc": False},
        ]
    )

    assert result.number == "5550000002"
    assert result.dnc_status == "clear"
    assert result.dnc_source == "batchdata"


def test_batchdata_phone_selection_marks_all_dnc_pool_blocked():
    result = batchdata_service._best_phone_result(
        [
            {"number": "5550000001", "type": "Mobile", "score": 90, "dnc": True},
            {"number": "5550000002", "type": "Landline", "score": 99, "dnc": True},
        ]
    )

    assert result.number == "5550000001"
    assert result.dnc_status == "blocked"
    assert result.dnc_source == "batchdata"


def test_batchdata_missing_dnc_flag_is_unknown_not_clear():
    result = batchdata_service._best_phone_result(
        [
            {"number": "5550000001", "type": "Mobile", "score": 90},
        ]
    )

    assert result.number == "5550000001"
    assert result.dnc_status == "unknown"
    assert result.dnc_source == "batchdata"


def test_dnc_gate_allows_only_clear_contacts():
    clear = EnrichedContact(filing=_filing(), phone="+12135550100", dnc_status="clear")
    blocked = EnrichedContact(filing=_filing(), phone="+12135550100", dnc_status="blocked")
    unknown = EnrichedContact(filing=_filing(), phone="+12135550100", dnc_status="unknown")

    assert dnc_service.can_call(clear).allowed is True
    assert dnc_service.can_call(blocked).allowed is False
    assert dnc_service.can_call(unknown).allowed is False


def test_enrichment_payload_includes_dnc_metadata():
    contact = EnrichedContact(
        filing=_filing(),
        phone="+12135550100",
        email="owner@example.test",
        estimated_rent=2000,
        property_type="residential",
        dnc_status="clear",
        dnc_source="batchdata",
    )

    payload = dedup_service._enrichment_payload(contact)

    assert payload["dnc_status"] == "clear"
    assert payload["dnc_source"] == "batchdata"
    assert "dnc_checked_at" in payload


def test_enrichment_payload_includes_language_hint():
    contact = EnrichedContact(
        filing=_filing(),
        phone="+12135550100",
        language_hint="spanish_likely",
    )

    payload = dedup_service._enrichment_payload(contact)

    assert payload["language_hint"] == "spanish_likely"


def test_manual_dnc_payload_includes_audit_fields():
    payload = dedup_service._manual_dnc_payload(
        source="business_record",
        notes="Reviewed public filing context",
    )

    assert payload["dnc_status"] == "clear"
    assert payload["dnc_source"] == "manual_override:business_record"
    assert payload["dnc_override_source"] == "business_record"
    assert payload["dnc_override_notes"] == "Reviewed public filing context"
    assert "dnc_checked_at" in payload
    assert "dnc_override_at" in payload
