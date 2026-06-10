from dashboard.main import _queue_response


def test_queue_response_sorts_by_rent_desc_and_pages():
    rows = [
        {"case_number": "LOW", "estimated_rent": 1200, "score": 30},
        {"case_number": "MISSING", "estimated_rent": None, "score": 99},
        {"case_number": "HIGH", "estimated_rent": 2500, "score": 50},
    ]

    payload = _queue_response(rows, limit=2, offset=0, sort="rent", direction="desc")

    assert payload["total"] == 3
    assert payload["limit"] == 2
    assert payload["offset"] == 0
    assert [r["case_number"] for r in payload["rows"]] == ["HIGH", "LOW"]


def test_queue_response_sorts_by_court_date_asc_and_offsets():
    rows = [
        {"case_number": "B", "court_date": "2026-06-20"},
        {"case_number": "NO_DATE", "court_date": None},
        {"case_number": "A", "court_date": "2026-06-10"},
    ]

    payload = _queue_response(rows, limit=1, offset=1, sort="court_date", direction="asc")

    assert payload["total"] == 3
    assert [r["case_number"] for r in payload["rows"]] == ["B"]
