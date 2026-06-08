# tests/test_harris_judgments.py
from datetime import date
from models.judgment import JudgmentRecord


def test_judgment_record_to_row_roundtrip():
    rec = JudgmentRecord(
        case_number="261100242063",
        defendant_name="Mariah Taylor",
        property_address="1617 Fannin Street Apt 1811, Houston, TX 77002",
        plaintiff_name="Houston House LP",
        judgment_date=date(2026, 6, 1),
        judgment_in_favor_of="Houston House LP",
        judgment_against="Mariah Taylor",
        disposition_desc="Default Judgment",
        disposition_date=date(2026, 6, 1),
        source_url="https://jpwebsite.harriscountytx.gov/PublicExtracts/search.jsp",
    )
    row = rec.to_row()
    assert row["case_number"] == "261100242063"
    assert row["window_tag"] == "W1"
    assert row["judgment_date"] == "2026-06-01"   # ISO string for Supabase
    assert row["state"] == "TX" and row["county"] == "Harris"
    assert row["prior_phone"] is False
