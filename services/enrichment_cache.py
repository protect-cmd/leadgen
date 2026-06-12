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
            columns = {
                row[1] for row in con.execute("PRAGMA table_info(searchbug_cache)")
            }
            if not columns:
                self._create_searchbug_cache(con)
            elif "postal" not in columns or "query_address" not in columns:
                con.execute("DROP TABLE IF EXISTS searchbug_cache_v2")
                self._create_searchbug_cache(con, table_name="searchbug_cache_v2")
                con.execute("""
                    INSERT OR REPLACE INTO searchbug_cache_v2
                    (first_name, last_name, city, state, postal, query_address,
                     phone, address, cached_at)
                    SELECT first_name, last_name, city, state, '', '',
                           phone, address, cached_at
                    FROM searchbug_cache
                """)
                con.execute("DROP TABLE searchbug_cache")
                con.execute("ALTER TABLE searchbug_cache_v2 RENAME TO searchbug_cache")
            cap_cols = {row[1] for row in con.execute("PRAGMA table_info(daily_cap)")}
            if not cap_cols:
                con.execute("""
                    CREATE TABLE daily_cap (
                        date  TEXT NOT NULL,
                        kind  TEXT NOT NULL DEFAULT 'searchbug',
                        count INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY (date, kind)
                    )
                """)
            elif "kind" not in cap_cols:
                # migrate the old (date PRIMARY KEY) table to (date, kind)
                con.execute("ALTER TABLE daily_cap RENAME TO daily_cap_v1")
                con.execute("""
                    CREATE TABLE daily_cap (
                        date  TEXT NOT NULL,
                        kind  TEXT NOT NULL DEFAULT 'searchbug',
                        count INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY (date, kind)
                    )
                """)
                con.execute(
                    "INSERT INTO daily_cap (date, kind, count) "
                    "SELECT date, 'searchbug', count FROM daily_cap_v1"
                )
                con.execute("DROP TABLE daily_cap_v1")
            con.execute("""
                CREATE TABLE IF NOT EXISTS alert_dedupe (
                    key  TEXT NOT NULL,
                    date TEXT NOT NULL,
                    PRIMARY KEY (key, date)
                )
            """)
            con.execute("""
                DELETE FROM searchbug_cache
                WHERE cached_at < ?
            """, (time.time() - _TTL_SECONDS,))

    @staticmethod
    def _create_searchbug_cache(
        con: sqlite3.Connection, table_name: str = "searchbug_cache"
    ) -> None:
        con.execute(f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                first_name    TEXT NOT NULL,
                last_name     TEXT NOT NULL,
                city          TEXT NOT NULL,
                state         TEXT NOT NULL,
                postal        TEXT NOT NULL DEFAULT '',
                query_address TEXT NOT NULL DEFAULT '',
                phone         TEXT,
                address       TEXT,
                cached_at     REAL NOT NULL,
                PRIMARY KEY (first_name, last_name, city, state, postal, query_address)
            )
        """)

    def _key(
        self,
        first: str,
        last: str,
        city: str,
        state: str,
        postal: str = "",
        query_address: str = "",
    ) -> tuple[str, str, str, str, str, str]:
        return (
            first.strip().lower(),
            last.strip().lower(),
            city.strip().lower(),
            state.strip().lower(),
            postal.strip().lower(),
            query_address.strip().lower(),
        )

    def get(
        self, first: str, last: str, city: str, state: str, *,
        postal: str = "", query_address: str = "",
    ) -> tuple[str | None, str | None] | None:
        """Return (phone, address) if cached and fresh; None if not cached or expired."""
        k = self._key(first, last, city, state, postal, query_address)
        cutoff = time.time() - _TTL_SECONDS
        with sqlite3.connect(self._db_path) as con:
            row = con.execute(
                "SELECT phone, address FROM searchbug_cache "
                "WHERE first_name=? AND last_name=? AND city=? AND state=? "
                "AND postal=? AND query_address=? AND cached_at>=?",
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
        *,
        postal: str = "",
        query_address: str = "",
    ) -> None:
        k = self._key(first, last, city, state, postal, query_address)
        with sqlite3.connect(self._db_path) as con:
            con.execute(
                "INSERT OR REPLACE INTO searchbug_cache "
                "(first_name, last_name, city, state, postal, query_address, "
                "phone, address, cached_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*k, phone, address, time.time()),
            )

    def check_daily_cap(self, cap: int, kind: str = "searchbug") -> bool:
        """True if under the daily cap for `kind` (OK to proceed)."""
        today = date.today().isoformat()
        with sqlite3.connect(self._db_path) as con:
            row = con.execute(
                "SELECT count FROM daily_cap WHERE date=? AND kind=?", (today, kind)
            ).fetchone()
        return (row[0] if row else 0) < cap

    def increment_daily_count(self, kind: str = "searchbug") -> None:
        today = date.today().isoformat()
        with sqlite3.connect(self._db_path) as con:
            con.execute(
                "INSERT INTO daily_cap (date, kind, count) VALUES (?, ?, 1) "
                "ON CONFLICT(date, kind) DO UPDATE SET count = count + 1",
                (today, kind),
            )

    def claim_alert_once_today(self, key: str) -> bool:
        """Atomically claim an alert key for today. Returns True if the caller
        is the first today to claim it (and should fire the alert); False if
        an earlier call already claimed it (alert was already sent today).
        Used to throttle SearchBug cap / account_error alerts to once/day.
        """
        today = date.today().isoformat()
        with sqlite3.connect(self._db_path) as con:
            try:
                con.execute(
                    "INSERT INTO alert_dedupe (key, date) VALUES (?, ?)",
                    (key, today),
                )
                return True
            except sqlite3.IntegrityError:
                return False


_default_cache: EnrichmentCache | None = None


def get_cache() -> EnrichmentCache:
    """Singleton SearchBug cache. SEARCHBUG_CACHE_DB_PATH overrides the default
    'data/enrichment_cache.db'. On Railway, point this at the persistent volume
    (e.g. /data/dnc/enrichment_cache.db) so the daily cap and cache survive
    redeploys.
    """
    global _default_cache
    if _default_cache is None:
        db_path = os.environ.get("SEARCHBUG_CACHE_DB_PATH", "data/enrichment_cache.db")
        _default_cache = EnrichmentCache(db_path=db_path)
    return _default_cache


def reset_cache_for_tests() -> None:
    """Test-only helper to drop the singleton (so a different db_path takes effect)."""
    global _default_cache
    _default_cache = None
