from datetime import date

from models.judgment import JudgmentRecord
from scrapers.texas.harris_debt_judgments import (
    is_default_judgment,
    to_garnishment_record,
    TX_VACATE_WINDOW_DAYS,
)


def _jr(**kw):
    base = dict(
        case_number="264100200001",
        defendant_name="Francisco Rodriguez",
        property_address="13239 Barnesworth Dr, Houston, TX 77049",
        plaintiff_name="Synchrony Bank",
        judgment_date=date(2026, 6, 12),
        judgment_in_favor_of="Synchrony Bank",
        judgment_against="Rodriguez, Francisco",
        disposition_desc="Default Judgment (OCA)",
    )
    base.update(kw)
    return JudgmentRecord(**base)


def test_default_judgment_detected():
    assert is_default_judgment(_jr()) is True
    assert is_default_judgment(_jr(disposition_desc="Agreed Judgment (OCA)")) is False
    assert is_default_judgment(_jr(disposition_desc=None)) is False


def test_window_constant_is_thirty():
    assert TX_VACATE_WINDOW_DAYS == 30


def test_maps_debtor_and_creditor_and_address():
    gr = to_garnishment_record(_jr())
    assert gr.debtor_name == "Francisco Rodriguez"
    assert gr.debtor_address == "13239 Barnesworth Dr, Houston, TX 77049"
    assert gr.creditor_name == "Synchrony Bank"
    assert gr.garnishee_name is None
    assert gr.state == "TX"
    assert gr.county == "Harris"
    assert gr.garnishment_type == "default_judgment"


def test_exemption_deadline_is_judgment_plus_30():
    gr = to_garnishment_record(_jr(judgment_date=date(2026, 6, 12)))
    assert gr.filing_date == date(2026, 6, 12)
    assert gr.exemption_deadline == date(2026, 7, 12)  # +30 days


def test_creditor_falls_back_to_plaintiff():
    gr = to_garnishment_record(_jr(judgment_in_favor_of=None, plaintiff_name="LVNV Funding LLC"))
    assert gr.creditor_name == "LVNV Funding LLC"


def test_to_row_roundtrips_for_storage():
    # the mapped record must serialize cleanly for gp_store.upsert_order
    row = to_garnishment_record(_jr()).to_row()
    assert row["case_number"] == "264100200001"
    assert row["exemption_deadline"] == "2026-07-12"
    assert row["garnishment_type"] == "default_judgment"
