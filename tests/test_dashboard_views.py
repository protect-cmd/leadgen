from services import dedup_service


class Query:
    def __init__(self):
        self.calls: list[tuple[str, str, str | None]] = []

    def eq(self, column: str, value: str):
        self.calls.append(("eq", column, value))
        return self

    def or_(self, value: str):
        self.calls.append(("or", value, None))
        return self


def test_spanish_residential_dashboard_view_filters_by_bucket_and_language_hint():
    query = Query()

    result = dedup_service._filter_dashboard_query(query, "spanish_residential")

    assert result is query
    assert query.calls == [
        ("eq", "lead_bucket", "residential_approved"),
        ("eq", "language_hint", "spanish_likely"),
    ]


def test_default_residential_dashboard_view_excludes_spanish_likely():
    query = Query()

    dedup_service._filter_dashboard_query(query, "residential_approved")

    assert query.calls == [
        ("eq", "lead_bucket", "residential_approved"),
        ("or", "language_hint.is.null,language_hint.neq.spanish_likely", None),
    ]


def test_spanish_dashboard_counts_are_subset_counts():
    counts = dedup_service._dashboard_counts_from_rows(
        [
            {"lead_bucket": "residential_approved", "language_hint": "spanish_likely"},
            {"lead_bucket": "residential_approved", "language_hint": None},
            {"lead_bucket": "commercial", "language_hint": "spanish_likely"},
            {"lead_bucket": "discarded", "language_hint": "spanish_likely"},
        ]
    )

    assert counts["residential_approved"] == 1
    assert counts["commercial"] == 0
    assert counts["spanish_residential"] == 1
    assert counts["spanish_commercial"] == 1
