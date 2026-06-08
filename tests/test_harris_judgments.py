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


from pathlib import Path
from scrapers.texas.harris_judgments import parse_judgments_csv

FIXTURE = Path("tests/fixtures/harris_judgments_sample.csv").read_text(encoding="utf-8")


def test_parse_keeps_only_tenant_lost_with_full_address():
    recs = parse_judgments_csv(FIXTURE)
    assert len(recs) == 3, f"expected 3 tenant-lost rows, got {len(recs)}: {[r.case_number for r in recs]}"
    for r in recs:
        assert r.judgment_against, f"judgment_against empty for {r.case_number}"
        assert r.property_address, f"property_address empty for {r.case_number}"
        assert r.window == "W1"


def test_parse_drops_defendant_wins_dismissed_blank_address_and_entities():
    recs = parse_judgments_csv(FIXTURE)
    case_nums = {r.case_number for r in recs}
    assert "261200080765" not in case_nums, "defendant-win row must be dropped"
    assert "999000000004" not in case_nums, "dismissed/no-judgment row must be dropped"
    assert "999000000005" not in case_nums, "blank-address row must be dropped"
    assert "261200072248" not in case_nums, "occupant-entity row must be dropped"
    assert "999000000007" not in case_nums, "LLC entity row must be dropped"
    assert len(recs) == 3
