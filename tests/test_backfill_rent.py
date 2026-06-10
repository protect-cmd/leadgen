from scripts.backfill_rent import (
    _apply_extracted_date_filter,
    _order_scored_backfill_rows,
    _prepare_ists_backfill_rows,
    rentometer_median,
)


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


def test_apply_extracted_date_filter_uses_half_open_day():
    calls = []

    class Query:
        def gte(self, column, value):
            calls.append(("gte", column, value))
            return self

        def lt(self, column, value):
            calls.append(("lt", column, value))
            return self

    q = _apply_extracted_date_filter(Query(), "scraped_at", "2026-06-10")

    assert isinstance(q, Query)
    assert calls == [
        ("gte", "scraped_at", "2026-06-10T00:00:00"),
        ("lt", "scraped_at", "2026-06-11T00:00:00"),
    ]


def test_rentometer_median_fails_fast_on_payment_required(monkeypatch):
    import urllib.error
    import urllib.request

    monkeypatch.setenv("RENTOMETER_API_KEY", "test-key")

    def raise_402(*args, **kwargs):
        raise urllib.error.HTTPError(
            url="https://example.test",
            code=402,
            msg="Payment Required",
            hdrs={},
            fp=None,
        )

    monkeypatch.setattr(urllib.request, "urlopen", raise_402)

    try:
        rentometer_median("123 Main St, Houston, TX 77002")
    except RuntimeError as exc:
        assert "402" in str(exc)
    else:
        raise AssertionError("Expected RuntimeError")
