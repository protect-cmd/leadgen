from datetime import date
from models.garnishment import GarnishmentRecord


def test_to_row_serializes_dates_and_keys():
    rec = GarnishmentRecord(
        case_number="2026-001234-CC",
        debtor_name="MARIA GOMEZ",
        debtor_address="123 SW 8th St, Miami, FL 33130",
        creditor_name="MIDLAND CREDIT MGMT",
        garnishee_name="ACME LOGISTICS INC",
        filing_date=date(2026, 6, 15),
        exemption_deadline=date(2026, 7, 5),
        source_url="https://example/ocs/case/2026-001234-CC",
    )
    row = rec.to_row()
    assert row["case_number"] == "2026-001234-CC"
    assert row["debtor_name"] == "MARIA GOMEZ"
    assert row["state"] == "FL"
    assert row["county"] == "Miami-Dade"
    assert row["garnishment_type"] == "wage"
    assert row["filing_date"] == "2026-06-15"
    assert row["exemption_deadline"] == "2026-07-05"


def test_to_row_handles_null_dates():
    rec = GarnishmentRecord(
        case_number="X", debtor_name="A B", debtor_address="addr",
    )
    row = rec.to_row()
    assert row["filing_date"] is None
    assert row["exemption_deadline"] is None
    assert row["creditor_name"] is None
