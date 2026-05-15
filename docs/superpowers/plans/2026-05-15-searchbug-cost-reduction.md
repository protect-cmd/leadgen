# SearchBug Cost Reduction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lower SearchBug people-search cost from ~$3/usable phone to <$1 by adding name parsing, a common-surname pre-call filter, ZIP narrowing, a 30-day SQLite cache, and a daily call cap.

**Architecture:** New `services/name_utils.py` provides `parse_name`, `split_tenants`, `is_common_surname`, and `resolve_zip`. New `services/enrichment_cache.py` wraps SQLite for hit/miss caching and daily cap tracking. `batchdata_service.enrich_tenant_by_name` is rewritten to pipe through these layers before calling SearchBug.

**Tech Stack:** Python 3.11+, sqlite3 (stdlib), pytest, pytest-asyncio, pydantic (existing)

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `services/name_utils.py` | Create | Name parsing, tenant splitting, surname filter, ZIP map |
| `services/enrichment_cache.py` | Create | SQLite cache (hits + misses), daily cap |
| `services/batchdata_service.py` | Modify | `enrich_tenant_by_name` rewrite |
| `tests/test_name_utils.py` | Create | Unit tests for name_utils |
| `tests/test_enrichment_cache.py` | Create | Unit tests for enrichment_cache |
| `tests/test_batchdata_yellow_enrichment.py` | Create | Integration tests for updated `enrich_tenant_by_name` |
| `.env.example` | Modify | Add `SEARCHBUG_DAILY_CAP=100` |
| `.gitignore` | Modify | Add `data/` directory (SQLite cache lives here) |

---

## Task 1: `services/name_utils.py` — parse_name and split_tenants

**Files:**
- Create: `tests/test_name_utils.py`
- Create: `services/name_utils.py`

- [ ] **Step 1: Write failing tests for parse_name**

Create `tests/test_name_utils.py`:

```python
from __future__ import annotations

import pytest
from services.name_utils import parse_name, split_tenants


class TestParseName:
    def test_last_comma_first(self):
        assert parse_name("JOHNSON, MARY") == ("MARY", "JOHNSON")

    def test_last_comma_first_middle(self):
        # Middle name stripped
        assert parse_name("JOHNSON, MARY ANN") == ("MARY", "JOHNSON")

    def test_last_comma_first_initial(self):
        # Middle initial stripped
        assert parse_name("LILLY, BRETT L") == ("BRETT", "LILLY")

    def test_last_comma_first_initial_dot(self):
        assert parse_name("LILLY, BRETT L.") == ("BRETT", "LILLY")

    def test_first_last(self):
        assert parse_name("JOHN SMITH") == ("JOHN", "SMITH")

    def test_first_middle_last(self):
        # Middle name stripped
        assert parse_name("BRETT L LILLY") == ("BRETT", "LILLY")

    def test_first_middle_initial_dot_last(self):
        assert parse_name("BRETT L. LILLY") == ("BRETT", "LILLY")

    def test_single_token(self):
        assert parse_name("JOHN") == ("", "")

    def test_empty_string(self):
        assert parse_name("") == ("", "")

    def test_whitespace_only(self):
        assert parse_name("   ") == ("", "")

    def test_lowercase_preserved(self):
        # parse_name does not uppercase — callers handle casing
        first, last = parse_name("john smith")
        assert first == "john"
        assert last == "smith"


class TestSplitTenants:
    def test_single_person(self):
        assert split_tenants("JOHN SMITH") == ["JOHN SMITH"]

    def test_three_tokens_not_split(self):
        # Could be first+middle+last — not split
        assert split_tenants("BRETT L LILLY") == ["BRETT L LILLY"]

    def test_four_tokens_split(self):
        assert split_tenants("AVONTE THOMAS ASHANTE JOHNSON") == [
            "AVONTE THOMAS",
            "ASHANTE JOHNSON",
        ]

    def test_four_tokens_with_initial_not_split(self):
        # Token[1] is a single char (middle initial) → treat as single person
        assert split_tenants("JOHN A SMITH DOE") == ["JOHN A SMITH DOE"]

    def test_empty_string(self):
        assert split_tenants("") == [""]
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_name_utils.py -v
```

Expected: `ModuleNotFoundError: No module named 'services.name_utils'`

- [ ] **Step 3: Implement parse_name and split_tenants**

Create `services/name_utils.py`:

```python
from __future__ import annotations

_MIDDLE_INITIAL_RE = None  # built lazily

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}


def _is_middle_initial(token: str) -> bool:
    """Single letter or single letter followed by a period."""
    t = token.rstrip(".")
    return len(t) == 1


def parse_name(raw: str) -> tuple[str, str]:
    """Parse a raw court name into (first_name, last_name).

    Handles:
    - "LAST, FIRST"
    - "LAST, FIRST MIDDLE"  → middle stripped
    - "FIRST LAST"
    - "FIRST MIDDLE LAST"   → middle stripped
    """
    raw = raw.strip()
    if not raw:
        return "", ""

    if "," in raw:
        # "LAST, FIRST [MIDDLE...]"
        last, _, rest = raw.partition(",")
        last = last.strip()
        parts = rest.strip().split()
        first = parts[0] if parts else ""
        return (first, last) if first and last else ("", "")

    # Space-separated: "FIRST [MIDDLE] LAST" or "FIRST LAST"
    tokens = raw.split()
    if len(tokens) < 2:
        return "", ""

    first = tokens[0]
    last = tokens[-1]

    # If there are middle tokens and last == middle initial, this is ambiguous;
    # trust first + last (first and last token) regardless.
    return first, last


def split_tenants(raw: str) -> list[str]:
    """Split a multi-tenant string into individual name strings.

    Only splits 4-token strings where no token looks like a middle initial
    (single character or single char + dot). All other strings returned as-is.

    Examples:
        "AVONTE THOMAS ASHANTE JOHNSON" → ["AVONTE THOMAS", "ASHANTE JOHNSON"]
        "BRETT L LILLY"                 → ["BRETT L LILLY"]
    """
    tokens = raw.strip().split()
    if len(tokens) == 4 and not any(_is_middle_initial(t) for t in tokens):
        return [" ".join(tokens[:2]), " ".join(tokens[2:])]
    return [raw]
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_name_utils.py::TestParseName tests/test_name_utils.py::TestSplitTenants -v
```

Expected: all green

- [ ] **Step 5: Commit**

```bash
git add services/name_utils.py tests/test_name_utils.py
git commit -m "feat: add name_utils parse_name and split_tenants"
```

---

## Task 2: `services/name_utils.py` — is_common_surname and resolve_zip

**Files:**
- Modify: `tests/test_name_utils.py` (add new test classes)
- Modify: `services/name_utils.py` (add two functions)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_name_utils.py`:

```python
from services.name_utils import is_common_surname, resolve_zip


class TestIsCommonSurname:
    def test_smith_is_common(self):
        assert is_common_surname("smith") is True

    def test_uppercase_smith_is_common(self):
        assert is_common_surname("SMITH") is True

    def test_johnson_is_common(self):
        assert is_common_surname("JOHNSON") is True

    def test_uncommon_surname(self):
        assert is_common_surname("kowalczyk") is False

    def test_empty_string(self):
        assert is_common_surname("") is False


class TestResolveZip:
    def test_cincinnati_oh(self):
        assert resolve_zip("Cincinnati", "OH") == "45202"

    def test_case_insensitive(self):
        assert resolve_zip("CINCINNATI", "oh") == "45202"

    def test_atlanta_ga(self):
        assert resolve_zip("Atlanta", "GA") == "30303"

    def test_unknown_city(self):
        assert resolve_zip("Nowheresville", "XX") == ""

    def test_nashville_tn(self):
        assert resolve_zip("Nashville", "TN") == "37201"
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_name_utils.py::TestIsCommonSurname tests/test_name_utils.py::TestResolveZip -v
```

Expected: `ImportError: cannot import name 'is_common_surname'`

- [ ] **Step 3: Implement is_common_surname and resolve_zip**

Append to `services/name_utils.py`:

```python
# Top-300 US Census 2010 surnames (lower-cased). Common names produce
# multi-match SearchBug responses we'd pay for but reject. Skip them.
_COMMON_SURNAMES: frozenset[str] = frozenset({
    "smith", "johnson", "williams", "brown", "jones", "garcia", "miller",
    "davis", "rodriguez", "martinez", "hernandez", "lopez", "gonzalez",
    "wilson", "anderson", "thomas", "taylor", "moore", "jackson", "martin",
    "lee", "perez", "thompson", "white", "harris", "sanchez", "clark",
    "ramirez", "lewis", "robinson", "walker", "young", "allen", "king",
    "wright", "scott", "torres", "nguyen", "hill", "flores", "green",
    "adams", "nelson", "baker", "hall", "rivera", "campbell", "mitchell",
    "carter", "roberts", "gomez", "phillips", "evans", "turner", "diaz",
    "parker", "cruz", "edwards", "collins", "reyes", "stewart", "morris",
    "morales", "murphy", "cook", "rogers", "gutierrez", "ortiz", "morgan",
    "cooper", "peterson", "bailey", "reed", "kelly", "howard", "ramos",
    "kim", "cox", "ward", "richardson", "watson", "brooks", "chavez",
    "wood", "james", "bennett", "gray", "mendoza", "ruiz", "hughes",
    "price", "alvarez", "castillo", "sanders", "patel", "myers", "long",
    "ross", "foster", "jimenez", "owens", "weaver", "price", "graves",
    "washington", "butler", "simmons", "foster", "gonzales", "bryant",
    "alexander", "russell", "griffin", "diaz", "hayes", "myers", "ford",
    "hamilton", "graham", "sullivan", "wallace", "woods", "cole", "west",
    "jordan", "owens", "reynolds", "fisher", "ellis", "harrison", "gibson",
    "mcdonald", "cruz", "marshall", "ortega", "gonzales", "freeman",
    "wells", "webb", "simpson", "stevens", "tucker", "porter", "hunter",
    "hicks", "crawford", "henry", "boyd", "mason", "moreno", "kennedy",
    "warren", "dixon", "ramos", "reyes", "burns", "gordon", "shaw",
    "holmes", "rice", "robertson", "hunt", "black", "daniels", "palmer",
    "mills", "nichols", "grant", "knight", "ferguson", "rose", "stone",
    "hawkins", "dunn", "perkins", "hudson", "spencer", "gardner", "stephens",
    "payne", "pierce", "berry", "matthews", "arnold", "wagner", "willis",
    "ray", "watkins", "olson", "carroll", "duncan", "snyder", "hart",
    "cunningham", "bradley", "lane", "andrews", "ruiz", "harper", "fox",
    "riley", "armstrong", "crane", "gordon", "austin", "shaw", "pope",
})


def is_common_surname(last_name: str) -> bool:
    """Return True if last_name is in the top-300 US Census surnames."""
    return last_name.strip().lower() in _COMMON_SURNAMES


# Representative ZIP codes for yellow-source cities (city.lower(), state.upper()) → ZIP
_CITY_ZIP: dict[tuple[str, str], str] = {
    ("cincinnati", "OH"): "45202",
    ("cleveland", "OH"): "44113",
    ("dayton", "OH"): "45402",
    ("columbus", "OH"): "43215",
    ("atlanta", "GA"): "30303",
    ("griffin", "GA"): "30223",
    ("marietta", "GA"): "30060",
    ("decatur", "GA"): "30030",
    ("chattanooga", "TN"): "37402",
    ("gallatin", "TN"): "37066",
    ("knoxville", "TN"): "37902",
    ("nashville", "TN"): "37201",
    ("phoenix", "AZ"): "85001",
    ("scottsdale", "AZ"): "85251",
    ("las vegas", "NV"): "89101",
    ("reno", "NV"): "89501",
    ("austin", "TX"): "78701",
}


def resolve_zip(city: str, state: str) -> str:
    """Return a representative ZIP for a known yellow-source city, or '' if unknown."""
    key = (city.strip().lower(), state.strip().upper())
    return _CITY_ZIP.get(key, "")
```

- [ ] **Step 4: Run all name_utils tests**

```
pytest tests/test_name_utils.py -v
```

Expected: all green

- [ ] **Step 5: Commit**

```bash
git add services/name_utils.py tests/test_name_utils.py
git commit -m "feat: add is_common_surname and resolve_zip to name_utils"
```

---

## Task 3: `services/enrichment_cache.py` — SQLite cache get/set

**Files:**
- Create: `tests/test_enrichment_cache.py`
- Create: `services/enrichment_cache.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_enrichment_cache.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_enrichment_cache.py -v
```

Expected: `ModuleNotFoundError: No module named 'services.enrichment_cache'`

- [ ] **Step 3: Implement EnrichmentCache**

Create `services/enrichment_cache.py`:

```python
from __future__ import annotations

import os
import sqlite3
import time
from datetime import date

_TTL_SECONDS = 30 * 86400  # 30 days


class EnrichmentCache:
    def __init__(self, db_path: str = "data/enrichment_cache.db") -> None:
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
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
```

- [ ] **Step 4: Run cache tests**

```
pytest tests/test_enrichment_cache.py::TestCacheGetSet -v
```

Expected: all green

- [ ] **Step 5: Commit**

```bash
git add services/enrichment_cache.py tests/test_enrichment_cache.py
git commit -m "feat: add enrichment_cache SQLite get/set with 30-day TTL"
```

---

## Task 4: `enrichment_cache.py` — daily cap tests

**Files:**
- Modify: `tests/test_enrichment_cache.py` (add new test class)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_enrichment_cache.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they pass immediately** (implementation already in place from Task 3)

```
pytest tests/test_enrichment_cache.py::TestDailyCap -v
```

Expected: all green (the implementation was included in Task 3)

- [ ] **Step 3: Run full cache test suite**

```
pytest tests/test_enrichment_cache.py -v
```

Expected: all green

- [ ] **Step 4: Commit**

```bash
git add tests/test_enrichment_cache.py
git commit -m "test: add daily cap tests for enrichment_cache"
```

---

## Task 5: Update `batchdata_service.enrich_tenant_by_name`

**Files:**
- Create: `tests/test_batchdata_yellow_enrichment.py`
- Modify: `services/batchdata_service.py` (replace `enrich_tenant_by_name` body)
- Modify: `.env.example` (add `SEARCHBUG_DAILY_CAP`)
- Modify: `.gitignore` (add `data/`)

- [ ] **Step 1: Write failing tests**

Create `tests/test_batchdata_yellow_enrichment.py`:

```python
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.contact import EnrichedContact
from models.filing import Filing
from services import batchdata_service
from services.enrichment_cache import EnrichmentCache


def _filing(**kwargs) -> Filing:
    values = {
        "case_number": "TEST-YELLOW-001",
        "tenant_name": "Brett Lilly",
        "property_address": "Cincinnati, OH",
        "landlord_name": "Apex LLC",
        "filing_date": date(2026, 5, 15),
        "state": "OH",
        "county": "Hamilton",
        "notice_type": "Eviction",
        "source_url": "https://example.test",
    }
    values.update(kwargs)
    return Filing(**values)


@pytest.fixture
def mock_cache(tmp_path):
    return EnrichmentCache(db_path=str(tmp_path / "test.db"))


@pytest.fixture(autouse=True)
def set_api_key(monkeypatch):
    monkeypatch.setenv("BATCHDATA_API_KEY", "test-key")
    monkeypatch.setenv("SEARCHBUG_CO_CODE", "test-co")
    monkeypatch.setenv("SEARCHBUG_API_KEY", "test-key")
    monkeypatch.setenv("SEARCHBUG_DAILY_CAP", "100")


@pytest.mark.asyncio
async def test_common_surname_skips_searchbug(mock_cache):
    """Smith (common surname) → SearchBug never called, unenriched contact returned."""
    filing = _filing(tenant_name="JOHN SMITH")

    with patch("services.enrichment_cache.get_cache", return_value=mock_cache), \
         patch("services.searchbug_service.search_tenant", new_callable=AsyncMock) as mock_sb:
        result = await batchdata_service.enrich_tenant_by_name(filing)

    mock_sb.assert_not_called()
    assert result.phone is None
    assert result.track == "ng"


@pytest.mark.asyncio
async def test_cache_hit_skips_searchbug(mock_cache):
    """Cached phone hit → SearchBug never called."""
    filing = _filing(tenant_name="BRETT LILLY")
    mock_cache.set("brett", "lilly", "cincinnati", "oh", "5551234567", None)

    with patch("services.enrichment_cache.get_cache", return_value=mock_cache), \
         patch("services.searchbug_service.search_tenant", new_callable=AsyncMock) as mock_sb:
        result = await batchdata_service.enrich_tenant_by_name(filing)

    mock_sb.assert_not_called()
    assert result.phone == "5551234567"
    assert result.dnc_source == "searchbug"


@pytest.mark.asyncio
async def test_cache_miss_calls_searchbug_and_stores(mock_cache):
    """Cache miss → SearchBug called, result stored in cache."""
    filing = _filing(tenant_name="BRETT LILLY")

    with patch("services.enrichment_cache.get_cache", return_value=mock_cache), \
         patch("services.searchbug_service.search_tenant", new_callable=AsyncMock,
               return_value=("5559876543", None)) as mock_sb:
        result = await batchdata_service.enrich_tenant_by_name(filing)

    mock_sb.assert_called_once()
    assert result.phone == "5559876543"
    # Verify stored in cache
    cached = mock_cache.get("brett", "lilly", "cincinnati", "oh")
    assert cached == ("5559876543", None)


@pytest.mark.asyncio
async def test_searchbug_address_triggers_batchdata(mock_cache):
    """SearchBug returns address → enrich_tenant called with patched filing."""
    filing = _filing(tenant_name="BRETT LILLY")
    resolved = "123 Elm St, Cincinnati, OH 45202"

    mock_enriched = EnrichedContact(
        filing=filing, track="ng", phone="5550001111", dnc_status="clear", dnc_source="batchdata"
    )

    with patch("services.enrichment_cache.get_cache", return_value=mock_cache), \
         patch("services.searchbug_service.search_tenant", new_callable=AsyncMock,
               return_value=(None, resolved)), \
         patch("services.batchdata_service.enrich_tenant", new_callable=AsyncMock,
               return_value=mock_enriched) as mock_enrich:
        result = await batchdata_service.enrich_tenant_by_name(filing)

    mock_enrich.assert_called_once()
    patched_filing = mock_enrich.call_args[0][0]
    assert patched_filing.property_address == resolved
    assert result.phone == "5550001111"


@pytest.mark.asyncio
async def test_multi_tenant_tries_both_names(mock_cache):
    """4-token name split → second person match returned if first misses."""
    filing = _filing(tenant_name="AVONTE THOMAS ASHANTE JOHNSON")

    async def fake_searchbug(first, last, city, state, postal=""):
        if first.lower() == "avonte":
            return None, None
        if first.lower() == "ashante":
            return "5554445555", None
        return None, None

    with patch("services.enrichment_cache.get_cache", return_value=mock_cache), \
         patch("services.searchbug_service.search_tenant", side_effect=fake_searchbug):
        result = await batchdata_service.enrich_tenant_by_name(filing)

    assert result.phone == "5554445555"


@pytest.mark.asyncio
async def test_daily_cap_exceeded_skips_call(mock_cache, monkeypatch):
    """When daily cap is 0, SearchBug never called."""
    monkeypatch.setenv("SEARCHBUG_DAILY_CAP", "0")
    filing = _filing(tenant_name="BRETT LILLY")

    with patch("services.enrichment_cache.get_cache", return_value=mock_cache), \
         patch("services.searchbug_service.search_tenant", new_callable=AsyncMock) as mock_sb:
        result = await batchdata_service.enrich_tenant_by_name(filing)

    mock_sb.assert_not_called()
    assert result.phone is None


@pytest.mark.asyncio
async def test_zip_resolved_from_city(mock_cache):
    """Cincinnati OH → ZIP 45202 appended to SearchBug call."""
    filing = _filing(tenant_name="BRETT LILLY", property_address="Cincinnati, OH")

    with patch("services.enrichment_cache.get_cache", return_value=mock_cache), \
         patch("services.searchbug_service.search_tenant", new_callable=AsyncMock,
               return_value=(None, None)) as mock_sb:
        await batchdata_service.enrich_tenant_by_name(filing)

    call_kwargs = mock_sb.call_args
    assert call_kwargs.kwargs.get("postal") == "45202" or \
           (call_kwargs.args and "45202" in call_kwargs.args)


@pytest.mark.asyncio
async def test_middle_initial_parsed_correctly(mock_cache):
    """'BRETT L LILLY' → parse_name strips L → SearchBug gets first='BRETT' last='LILLY'."""
    filing = _filing(tenant_name="BRETT L LILLY")

    with patch("services.enrichment_cache.get_cache", return_value=mock_cache), \
         patch("services.searchbug_service.search_tenant", new_callable=AsyncMock,
               return_value=("5550009999", None)) as mock_sb:
        result = await batchdata_service.enrich_tenant_by_name(filing)

    first, last = mock_sb.call_args.args[:2]
    assert first.lower() == "brett"
    assert last.lower() == "lilly"
    assert result.phone == "5550009999"
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_batchdata_yellow_enrichment.py -v
```

Expected: failures showing old behavior (no cache, no name_utils)

- [ ] **Step 3: Replace enrich_tenant_by_name in batchdata_service.py**

In `services/batchdata_service.py`, replace the entire `enrich_tenant_by_name` function (lines 287–358) with:

```python
async def enrich_tenant_by_name(
    filing: Filing,
    lookup_property_if_missing: bool = True,
) -> EnrichedContact:
    """NG track for yellow calendar sources — no property address available.

    Chain: split_tenants → parse_name → cache → surname filter →
           ZIP resolve → daily cap → SearchBug → BatchData → cache store.
    """
    from services.name_utils import parse_name, split_tenants, is_common_surname, resolve_zip
    from services.searchbug_service import search_tenant as _searchbug_search
    from services.enrichment_cache import get_cache

    cache = get_cache()
    cap = int(os.environ.get("SEARCHBUG_DAILY_CAP", "100"))

    # Parse city/state from yellow-source address "City, STATE" or "City, STATE ZIP"
    raw_addr = (filing.property_address or "").strip()
    addr_parts = [p.strip() for p in raw_addr.split(",")]
    city = addr_parts[0] if addr_parts else ""
    state = filing.state
    postal_from_address = ""
    if len(addr_parts) >= 2:
        tokens = addr_parts[1].split()
        if tokens:
            state = tokens[0] or filing.state
        if len(tokens) >= 2 and tokens[1].isdigit():
            postal_from_address = tokens[1]

    for raw_name in split_tenants(filing.tenant_name.strip()):
        first_name, last_name = parse_name(raw_name)
        if not first_name or not last_name:
            log.info(f"enrich_tenant_by_name: unparseable name segment {raw_name!r} for {filing.case_number}")
            continue

        # Cache lookup — None = uncached; (None, None) = cached miss
        cached = cache.get(first_name, last_name, city, state)
        if cached is not None:
            phone, resolved_address = cached
            if resolved_address:
                patched = filing.model_copy(update={"property_address": resolved_address})
                result = await enrich_tenant(patched, lookup_property_if_missing=lookup_property_if_missing)
                if not result.phone and phone:
                    from dataclasses import replace as _dc_replace
                    result = _dc_replace(result, phone=phone, dnc_source="searchbug")
                return result
            if phone:
                return EnrichedContact(
                    filing=filing, track="ng", phone=phone,
                    dnc_status="unknown", dnc_source="searchbug",
                )
            continue  # cached miss — try next name

        # Pre-call common-surname filter
        if is_common_surname(last_name):
            log.info(
                f"enrich_tenant_by_name: common surname skip {last_name!r} "
                f"for {filing.case_number}"
            )
            cache.set(first_name, last_name, city, state, None, None)
            continue

        # Daily cap check
        if not cache.check_daily_cap(cap):
            log.warning(f"enrich_tenant_by_name: daily cap {cap} reached for {filing.case_number}")
            break

        # ZIP narrowing — use address-derived ZIP first, then city map
        postal = postal_from_address or resolve_zip(city, state)

        phone, resolved_address = await _searchbug_search(
            first_name, last_name, city=city, state=state, postal=postal
        )
        cache.increment_daily_count()
        cache.set(first_name, last_name, city, state, phone, resolved_address)

        if resolved_address:
            patched = filing.model_copy(update={"property_address": resolved_address})
            result = await enrich_tenant(patched, lookup_property_if_missing=lookup_property_if_missing)
            if not result.phone and phone:
                from dataclasses import replace as _dc_replace
                result = _dc_replace(result, phone=phone, dnc_source="searchbug")
            return result

        if phone:
            log.info(f"enrich_tenant_by_name: SearchBug phone-only hit for {filing.case_number}")
            return EnrichedContact(
                filing=filing, track="ng", phone=phone,
                dnc_status="unknown", dnc_source="searchbug",
            )

    log.info(f"enrich_tenant_by_name: no match for {filing.case_number}")
    return EnrichedContact(filing=filing, track="ng", phone=None, email=None,
                           dnc_status="unknown", dnc_source=None)
```

- [ ] **Step 4: Run new tests**

```
pytest tests/test_batchdata_yellow_enrichment.py -v
```

Expected: all green

- [ ] **Step 5: Confirm existing batchdata tests still pass**

```
pytest tests/test_batchdata_tenant_enrichment.py tests/test_batchdata_optimization.py -v
```

Expected: all green (enrich_tenant is unchanged)

- [ ] **Step 6: Add SEARCHBUG_DAILY_CAP to .env.example**

In `.env.example`, add after the `SEARCHBUG_API_KEY=` line:

```
SEARCHBUG_DAILY_CAP=100
```

- [ ] **Step 7: Add data/ to .gitignore**

Check whether `.gitignore` already excludes `data/`:

```
grep -n "^data" .gitignore
```

If not present, append to `.gitignore`:

```
data/
```

- [ ] **Step 8: Commit**

```bash
git add services/batchdata_service.py tests/test_batchdata_yellow_enrichment.py \
        .env.example .gitignore
git commit -m "feat: wire name parsing, surname filter, ZIP map, cache, and daily cap into enrich_tenant_by_name"
```

---

## Task 6: Full regression run

- [ ] **Step 1: Run the full test suite**

```
pytest tests/ -v --tb=short 2>&1 | tail -40
```

Expected: no new failures. If any existing tests fail, investigate before proceeding.

- [ ] **Step 2: Smoke test with Hamilton County scraper (no live API)**

```
python -m pytest tests/test_hamilton_scraper.py -v
```

Expected: green

- [ ] **Step 3: Commit if any stray fixes were needed**

Only commit if you made additional changes to resolve failures found in Step 1.

```bash
git add -p
git commit -m "fix: resolve test failures found during regression"
```

---

## Self-Review Checklist

- **parse_name("BRETT L LILLY")** → Task 1 test + implementation covers middle-initial strip ✓
- **split_tenants("AVONTE THOMAS ASHANTE JOHNSON")** → Task 1 test ✓
- **is_common_surname** → Task 2 ✓
- **resolve_zip** → Task 2, 17-entry map matches spec ✓
- **SQLite cache get/set + TTL** → Task 3 ✓
- **Daily cap check + increment** → Task 4 ✓
- **enrich_tenant_by_name rewrite** → Task 5, all 8 test scenarios ✓
- **Cache miss (None, None) distinguished from uncached (None)** → Task 3 + 5 ✓
- **`filing.model_copy`** used (Pydantic), not `dataclasses.replace` ✓
- **`dataclasses.replace`** used for `EnrichedContact` (dataclass) ✓
- **`os.environ.get("SEARCHBUG_DAILY_CAP", "100")`** read in `enrich_tenant_by_name` ✓
- **.env.example updated** → Task 5 ✓
- **data/ added to .gitignore** → Task 5 ✓
