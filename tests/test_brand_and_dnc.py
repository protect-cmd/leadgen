from __future__ import annotations

from datetime import date

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
