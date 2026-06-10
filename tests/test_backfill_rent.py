from scripts.backfill_rent import _order_scored_backfill_rows


def test_order_scored_backfill_rows_priority_first_then_fresh_and_cap():
    rows = [
        {"case_number": "OLD_TIER1", "priority_rank": 1, "filing_date": "2026-06-01"},
        {"case_number": "NO_TIER", "priority_rank": None, "filing_date": "2026-06-10"},
        {"case_number": "FRESH_TIER1", "priority_rank": 1, "filing_date": "2026-06-10"},
        {"case_number": "TIER2", "priority_rank": 2, "filing_date": "2026-06-10"},
    ]

    ordered = _order_scored_backfill_rows(rows, cap=3)

    assert [r["case_number"] for r in ordered] == [
        "FRESH_TIER1",
        "OLD_TIER1",
        "TIER2",
    ]
