from datetime import date

from pipeline.queue_builder import _person_key, _score_and_sort


def test_person_key_normalizes_name_and_zip():
    assert _person_key("SMITH, JOHN", "77002") == "john|smith|77002"
    assert _person_key("John Smith", "77002") == "john|smith|77002"


def test_person_key_matches_across_name_formats_same_zip():
    # "LAST, FIRST" (court) and "First Last" (filing) are the same person
    assert _person_key("Nwankwo, Ifeanyi", "77090") == _person_key("Ifeanyi Nwankwo", "77090")


def test_person_key_differs_by_zip():
    assert _person_key("John Smith", "77002") != _person_key("John Smith", "77004")


def test_person_key_none_when_unparseable():
    assert _person_key("Occupants", "77002") is None
    assert _person_key("", "77002") is None


def test_score_and_sort_uses_rent_and_freshness_window():
    today = date(2026, 6, 10)
    rows = [
        {
            "case_number": "NO_RENT",
            "tenant_name": "Ifeanyi Nwankwo",
            "filing_date": "2026-06-10",
            "priority_rank": 1,
            "estimated_rent": None,
        },
        {
            "case_number": "HIGH_RENT",
            "tenant_name": "Ifeanyi Nwankwo",
            "filing_date": "2026-06-10",
            "priority_rank": 1,
            "estimated_rent": 3500,
        },
        {
            "case_number": "STALE",
            "tenant_name": "Ifeanyi Nwankwo",
            "filing_date": "2026-06-03",
            "priority_rank": 1,
            "estimated_rent": 3500,
        },
    ]

    sorted_rows = _score_and_sort(rows, today, window=7)

    assert [r["case_number"] for r in sorted_rows] == [
        "HIGH_RENT",
        "STALE",
        "NO_RENT",
    ]
    assert sorted_rows[0]["score"] > sorted_rows[1]["score"] > sorted_rows[2]["score"]
