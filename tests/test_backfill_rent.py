from scripts.backfill_rent import _order_scored_backfill_rows, _prepare_ists_backfill_rows


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


def test_prepare_ists_backfill_rows_adds_priority_and_uses_judgment_date():
    rows = [
        {
            "case_number": "TAIL",
            "property_address": "1 Main St, Nowhere, TN 37000",
            "judgment_date": "2026-06-10",
        },
        {
            "case_number": "PRI",
            "property_address": "100 Main St, Houston, TX 77002",
            "judgment_date": "2026-06-01",
        },
    ]

    ordered = _prepare_ists_backfill_rows(rows, {"77002": (1, "Houston")}, cap=2)

    assert ordered[0]["case_number"] == "PRI"
    assert ordered[0]["priority_rank"] == 1
    assert ordered[0]["priority_metro"] == "Houston"
    assert ordered[0]["filing_date"] == "2026-06-01"
