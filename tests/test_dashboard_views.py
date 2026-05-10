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


# ── _filter_dashboard_query ──────────────────────────────────────────────────

def test_ec_residential_excludes_spanish():
    q = Query()
    dedup_service._filter_dashboard_query(q, "ec_residential")
    assert ("eq", "lead_bucket", "residential_approved") in q.calls
    assert any(c[0] == "or" for c in q.calls)


def test_ec_commercial_excludes_spanish():
    q = Query()
    dedup_service._filter_dashboard_query(q, "ec_commercial")
    assert ("eq", "lead_bucket", "commercial") in q.calls
    assert any(c[0] == "or" for c in q.calls)


def test_ec_held():
    q = Query()
    dedup_service._filter_dashboard_query(q, "ec_held")
    assert q.calls == [("eq", "lead_bucket", "held")]


def test_ec_discarded():
    q = Query()
    dedup_service._filter_dashboard_query(q, "ec_discarded")
    assert q.calls == [("eq", "lead_bucket", "discarded")]


def test_ng_residential_excludes_spanish():
    q = Query()
    dedup_service._filter_dashboard_query(q, "ng_residential")
    assert ("eq", "lead_bucket", "residential_approved") in q.calls
    assert any(c[0] == "or" for c in q.calls)


def test_ng_commercial_excludes_spanish():
    q = Query()
    dedup_service._filter_dashboard_query(q, "ng_commercial")
    assert ("eq", "lead_bucket", "commercial") in q.calls
    assert any(c[0] == "or" for c in q.calls)


def test_ng_spanish_residential():
    q = Query()
    dedup_service._filter_dashboard_query(q, "ng_spanish_residential")
    assert q.calls == [
        ("eq", "lead_bucket", "residential_approved"),
        ("eq", "language_hint", "spanish_likely"),
    ]


def test_ng_spanish_commercial():
    q = Query()
    dedup_service._filter_dashboard_query(q, "ng_spanish_commercial")
    assert q.calls == [
        ("eq", "lead_bucket", "commercial"),
        ("eq", "language_hint", "spanish_likely"),
    ]


def test_ng_held():
    q = Query()
    dedup_service._filter_dashboard_query(q, "ng_held")
    assert q.calls == [("eq", "lead_bucket", "held")]


def test_ng_discarded():
    q = Query()
    dedup_service._filter_dashboard_query(q, "ng_discarded")
    assert q.calls == [("eq", "lead_bucket", "discarded")]


# ── _track_for_dashboard_view ────────────────────────────────────────────────

def test_ec_views_return_ec_track():
    for view in ("ec_residential", "ec_commercial", "ec_held", "ec_discarded"):
        assert dedup_service._track_for_dashboard_view(view) == "ec", view


def test_ng_views_return_ng_track():
    for view in (
        "ng_residential", "ng_commercial",
        "ng_spanish_residential", "ng_spanish_commercial",
        "ng_held", "ng_discarded",
    ):
        assert dedup_service._track_for_dashboard_view(view) == "ng", view


# ── _ec_counts_from_rows ─────────────────────────────────────────────────────

def test_ec_counts_split_by_bucket():
    rows = [
        {"lead_bucket": "residential_approved", "language_hint": None},
        {"lead_bucket": "residential_approved", "language_hint": "spanish_likely"},  # spanish excluded from ec_residential
        {"lead_bucket": "commercial", "language_hint": None},
        {"lead_bucket": "held", "language_hint": None},
        {"lead_bucket": "discarded", "language_hint": None},
    ]
    counts = dedup_service._ec_counts_from_rows(rows)
    assert counts["ec_residential"] == 1
    assert counts["ec_commercial"] == 1
    assert counts["ec_held"] == 1
    assert counts["ec_discarded"] == 1


def test_ec_counts_spanish_residential_not_counted_in_ec_residential():
    rows = [
        {"lead_bucket": "residential_approved", "language_hint": "spanish_likely"},
        {"lead_bucket": "commercial", "language_hint": "spanish_likely"},
    ]
    counts = dedup_service._ec_counts_from_rows(rows)
    assert counts["ec_residential"] == 0
    assert counts["ec_commercial"] == 0


# ── _ng_counts_from_contact_rows ─────────────────────────────────────────────

def test_ng_counts_split_by_bucket_and_language():
    rows = [
        {"filings": {"lead_bucket": "residential_approved", "language_hint": None}},
        {"filings": {"lead_bucket": "residential_approved", "language_hint": "spanish_likely"}},
        {"filings": {"lead_bucket": "commercial", "language_hint": None}},
        {"filings": {"lead_bucket": "commercial", "language_hint": "spanish_likely"}},
        {"filings": {"lead_bucket": "held", "language_hint": None}},
        {"filings": {"lead_bucket": "discarded", "language_hint": None}},
    ]
    counts = dedup_service._ng_counts_from_contact_rows(rows)
    assert counts["ng_residential"] == 1
    assert counts["ng_spanish_residential"] == 1
    assert counts["ng_commercial"] == 1
    assert counts["ng_spanish_commercial"] == 1
    assert counts["ng_held"] == 1
    assert counts["ng_discarded"] == 1


def test_ng_counts_null_filings_skipped():
    rows = [{"filings": None}, {"filings": {}}]
    counts = dedup_service._ng_counts_from_contact_rows(rows)
    assert sum(counts.values()) == 0
