from services.enrichment_cache import EnrichmentCache


def test_separate_kinds_have_independent_counters(tmp_path):
    c = EnrichmentCache(db_path=str(tmp_path / "c.db"))
    assert c.check_daily_cap(2, kind="bland") is True
    c.increment_daily_count(kind="bland")
    c.increment_daily_count(kind="bland")
    assert c.check_daily_cap(2, kind="bland") is False     # bland hit its cap
    assert c.check_daily_cap(2, kind="searchbug") is True   # searchbug untouched


def test_searchbug_kind_is_the_default(tmp_path):
    c = EnrichmentCache(db_path=str(tmp_path / "c.db"))
    c.increment_daily_count()                 # no kind -> searchbug
    assert c.check_daily_cap(1) is False       # default kind counted it
    assert c.check_daily_cap(1, kind="bland") is True
