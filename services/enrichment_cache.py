from __future__ import annotations

import os
import sqlite3
import time
from datetime import date

_TTL_SECONDS = 30 * 86400  # 30 days


class EnrichmentCache:
    def __init__(self, db_path: str = "data/enrichment_cache.db") -> None:
        self._db_path = db_path
        dir_name = os.path.dirname(db_path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS searchbug_cache (
                    first_name TEXT NOT NULL,
                    last_name  TEXT NOT NULL,
                    city       TEXT NOT NULL,
                    state      TEXT NOT NULL,
                    phone      TEXT,
                    address    TEXT,
                    cached_at  REAL NOT NULL,
                    PRIMARY KEY (first_name, last_name, city, state)
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS daily_cap (
                    date  TEXT PRIMARY KEY,
                    count INTEGER NOT NULL DEFAULT 0
                )
            """)
            con.execute("""
                DELETE FROM searchbug_cache
                WHERE cached_at < ?
            """, (time.time() - _TTL_SECONDS,))

    def _key(self, first: str, last: str, city: str, state: str) -> tuple[str, str, str, str]:
        return first.lower(), last.lower(), city.lower(), state.lower()

    def get(
        self, first: str, last: str, city: str, state: str
    ) -> tuple[str | None, str | None] | None:
        """Return (phone, address) if cached and fresh; None if not cached or expired."""
        k = self._key(first, last, city, state)
        cutoff = time.time() - _TTL_SECONDS
        with sqlite3.connect(self._db_path) as con:
            row = con.execute(
                "SELECT phone, address FROM searchbug_cache "
                "WHERE first_name=? AND last_name=? AND city=? AND state=? AND cached_at>=?",
                (*k, cutoff),
            ).fetchone()
        if row is None:
            return None
        return row[0], row[1]

    def set(
        self,
        first: str,
        last: str,
        city: str,
        state: str,
        phone: str | None,
        address: str | None,
    ) -> None:
        k = self._key(first, last, city, state)
        with sqlite3.connect(self._db_path) as con:
            con.execute(
                "INSERT OR REPLACE INTO searchbug_cache "
                "(first_name, last_name, city, state, phone, address, cached_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (*k, phone, address, time.time()),
            )

    def check_daily_cap(self, cap: int) -> bool:
        """Return True if under the daily cap (OK to proceed), False if exceeded."""
        today = date.today().isoformat()
        with sqlite3.connect(self._db_path) as con:
            row = con.execute(
                "SELECT count FROM daily_cap WHERE date=?", (today,)
            ).fetchone()
        count = row[0] if row else 0
        return count < cap

    def increment_daily_count(self) -> None:
        today = date.today().isoformat()
        with sqlite3.connect(self._db_path) as con:
            con.execute(
                "INSERT INTO daily_cap (date, count) VALUES (?, 1) "
                "ON CONFLICT(date) DO UPDATE SET count = count + 1",
                (today,),
            )


_default_cache: EnrichmentCache | None = None


def get_cache() -> EnrichmentCache:
    global _default_cache
    if _default_cache is None:
        _default_cache = EnrichmentCache()
    return _default_cache
