from __future__ import annotations

import time
import pytest
from services.enrichment_cache import EnrichmentCache


@pytest.fixture
def cache(tmp_path):
    return EnrichmentCache(db_path=str(tmp_path / "test.db"))


class TestCacheGetSet:
    def test_miss_on_empty(self, cache):
        result = cache.get("john", "smith", "cincinnati", "oh")
        assert result is None

    def test_hit_after_set(self, cache):
        cache.set("john", "doe", "cincinnati", "oh", "5551234567", "123 Main St, Cincinnati, OH 45202")
        result = cache.get("john", "doe", "cincinnati", "oh")
        assert result == ("5551234567", "123 Main St, Cincinnati, OH 45202")

    def test_cached_miss_stored(self, cache):
        # Storing (None, None) means we already tried and got nothing
        cache.set("jane", "smith", "dayton", "oh", None, None)
        result = cache.get("jane", "smith", "dayton", "oh")
        assert result == (None, None)  # not None — it's a cached miss

    def test_key_is_case_insensitive(self, cache):
        cache.set("JOHN", "DOE", "CINCINNATI", "OH", "5551234567", None)
        result = cache.get("john", "doe", "cincinnati", "oh")
        assert result is not None
        assert result[0] == "5551234567"

    def test_expired_entry_returns_miss(self, cache):
        # Manually insert an entry with a timestamp 31 days ago
        import sqlite3, time as _time
        old_ts = _time.time() - (31 * 86400)
        with sqlite3.connect(cache._db_path) as con:
            con.execute(
                "INSERT OR REPLACE INTO searchbug_cache "
                "(first_name, last_name, city, state, phone, address, cached_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("expired", "user", "atlanta", "ga", "5550000001", None, old_ts),
            )
        result = cache.get("expired", "user", "atlanta", "ga")
        assert result is None

    def test_overwrite_updates_timestamp(self, cache):
        cache.set("john", "doe", "cincinnati", "oh", None, None)
        cache.set("john", "doe", "cincinnati", "oh", "5559998888", "456 Oak Ave")
        result = cache.get("john", "doe", "cincinnati", "oh")
        assert result == ("5559998888", "456 Oak Ave")


class TestDailyCap:
    def test_under_cap_returns_true(self, cache):
        assert cache.check_daily_cap(100) is True

    def test_at_cap_returns_false(self, cache):
        for _ in range(3):
            cache.increment_daily_count()
        assert cache.check_daily_cap(3) is False

    def test_one_under_cap_returns_true(self, cache):
        for _ in range(2):
            cache.increment_daily_count()
        assert cache.check_daily_cap(3) is True

    def test_increment_accumulates(self, cache):
        cache.increment_daily_count()
        cache.increment_daily_count()
        # Check internal count
        import sqlite3
        from datetime import date
        today = date.today().isoformat()
        with sqlite3.connect(cache._db_path) as con:
            row = con.execute("SELECT count FROM daily_cap WHERE date=?", (today,)).fetchone()
        assert row[0] == 2
