from services.enrichment_cache import EnrichmentCache


def test_ops_kv_round_trip(tmp_path):
    c = EnrichmentCache(db_path=str(tmp_path / "c.db"))
    assert c.get_ops_value("rentometer_credits") is None
    c.set_ops_value("rentometer_credits", "263")
    assert c.get_ops_value("rentometer_credits") == "263"
    c.set_ops_value("rentometer_credits", "250")     # overwrite
    assert c.get_ops_value("rentometer_credits") == "250"


def test_ops_kv_returns_value_and_updated_at(tmp_path):
    c = EnrichmentCache(db_path=str(tmp_path / "c.db"))
    c.set_ops_value("k", "v")
    val, updated_at = c.get_ops_value_with_ts("k")
    assert val == "v"
    assert isinstance(updated_at, str) and updated_at


def test_daily_count_reads_counter(tmp_path):
    c = EnrichmentCache(db_path=str(tmp_path / "c.db"))
    assert c.daily_count("bland") == 0
    c.increment_daily_count(kind="bland")
    assert c.daily_count("bland") == 1
