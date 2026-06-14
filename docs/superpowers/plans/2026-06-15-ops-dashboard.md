# Ops Dashboard (`/ops`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A single internal `/ops` page showing pipeline automation health (per-county scrapes, health flags, spend/caps) + per-track conversion funnel + 7-day trend — numbers not visible in `/search` or `/lists`.

**Architecture:** New `services/ops_stats.py` does all aggregation (one function per section, fault-isolated). `dashboard/main.py` adds `/ops` (HTML) + `/api/ops` (JSON) behind `require_queue`. `dashboard/ops.html` renders it. A new additive `lead_contacts.bland_triggered_at` column powers the fired/day trend; last-known Rentometer credits are cached in the enrichment-cache sqlite.

**Tech Stack:** Python 3.13, FastAPI, Supabase (PostgREST), sqlite (enrichment cache), pytest, vanilla HTML/CSS/JS (no chart libs).

**Spec:** `docs/superpowers/specs/2026-06-15-ops-dashboard-design.md`

**Branch / worktree:** `feat-ops-dashboard` off `origin/main` (already created at `C:/Users/Zeann/.config/superpowers/worktrees/leadgen/sched`).

**Test env:** mocked tests run with `SUPABASE_URL=https://test.supabase.co SUPABASE_SERVICE_ROLE_KEY=test-key python -m pytest …`. Pre-existing network/runner failures are unrelated.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `migrations/021_lead_contacts_bland_triggered_at.sql` (new) | additive nullable `bland_triggered_at` column |
| `services/dedup_service.py` (modify) | `set_bland_status` stamps `bland_triggered_at` (guarded by column discovery) |
| `services/enrichment_cache.py` (modify) | `ops_kv` table + `set_ops_value`/`get_ops_value`; used for last-known Rentometer credits |
| `scripts/backfill_rent.py` (modify) | record `credits_remaining` after each Rentometer call |
| `services/ops_stats.py` (new) | all section aggregations + `get_ops_stats` composer |
| `dashboard/main.py` (modify) | `GET /ops` + `GET /api/ops` |
| `dashboard/ops.html` (new) | render `/api/ops`, dark theme, Refresh |
| `tests/test_ops_stats.py` (new) | section + composer + pure-helper tests (with a fake Supabase) |
| `tests/test_bland_triggered_at.py` (new) | `set_bland_status` stamping + pre-migration guard |
| `tests/test_ops_kv.py` (new) | enrichment-cache ops_kv round-trip |

---

## Task 1: `bland_triggered_at` column + stamp on fire (guarded)

**Files:**
- Create: `migrations/021_lead_contacts_bland_triggered_at.sql`
- Modify: `services/dedup_service.py` (`set_bland_status`, ~line 662)
- Test: `tests/test_bland_triggered_at.py`

- [ ] **Step 1: Write the migration**

```sql
-- migrations/021_lead_contacts_bland_triggered_at.sql
-- Per-fire timestamp so the ops dashboard can chart fired/day for the NG track.
-- Additive + nullable + IF NOT EXISTS — safe to apply live, any time.
ALTER TABLE lead_contacts
    ADD COLUMN IF NOT EXISTS bland_triggered_at TIMESTAMPTZ;
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_bland_triggered_at.py
import pytest
from unittest.mock import MagicMock, patch
from services import dedup_service


def _client_capture():
    client = MagicMock()
    captured = {}
    upd = client.table.return_value.update
    def _update(payload):
        captured["lead_payload"] = payload if "ng_bland_status" not in payload else captured.get("lead_payload")
        # the lead_contacts write is the one whose payload has bland_status (not ng_*)
        if "bland_status" in payload:
            captured["lead_payload"] = payload
        return client.table.return_value.update.return_value
    upd.side_effect = _update
    client.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
    client.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
    return client, captured


@pytest.mark.asyncio
async def test_stamps_bland_triggered_at_when_column_known(monkeypatch):
    client, captured = _client_capture()
    monkeypatch.setattr(dedup_service, "_client", client)
    monkeypatch.setattr(dedup_service, "_lead_contact_known_columns",
                        lambda: {"bland_status", "bland_call_id", "bland_triggered_at"})
    await dedup_service.set_bland_status("CN1", "ng", "triggered", call_id="call-1")
    lp = captured["lead_payload"]
    assert lp["bland_call_id"] == "call-1"
    assert "bland_triggered_at" in lp and lp["bland_triggered_at"]


@pytest.mark.asyncio
async def test_omits_bland_triggered_at_when_column_unknown(monkeypatch):
    client, captured = _client_capture()
    monkeypatch.setattr(dedup_service, "_client", client)
    monkeypatch.setattr(dedup_service, "_lead_contact_known_columns",
                        lambda: {"bland_status", "bland_call_id"})  # column not present
    await dedup_service.set_bland_status("CN1", "ng", "triggered", call_id="call-1")
    lp = captured["lead_payload"]
    assert lp["bland_call_id"] == "call-1"      # still written
    assert "bland_triggered_at" not in lp        # not sent -> optional write won't be suppressed
```

- [ ] **Step 3: Run test to verify it fails**

Run: `SUPABASE_URL=… SUPABASE_SERVICE_ROLE_KEY=… python -m pytest tests/test_bland_triggered_at.py -v`
Expected: FAIL — `bland_triggered_at` never added to the lead payload.

- [ ] **Step 4: Implement the stamp**

In `services/dedup_service.py`, inside `set_bland_status._update`, where `lead_payload` is built (currently sets `bland_status` and, if `call_id`, `bland_call_id`), add the guarded timestamp:

```python
        lead_payload: dict = {"bland_status": status}
        if call_id:
            payload[col_call_id] = call_id
            lead_payload["bland_call_id"] = call_id
            # Per-fire timestamp for the ops dashboard's fired/day trend. Guard on
            # column discovery: _execute_optional_lead_contact_write suppresses the
            # WHOLE write on error, so sending an unknown column would silently drop
            # bland_call_id too (breaking fire idempotency) until migration 021 lands.
            if "bland_triggered_at" in _lead_contact_known_columns():
                lead_payload["bland_triggered_at"] = datetime.now(timezone.utc).isoformat()
```

(`datetime`, `timezone` are already imported at the top of `dedup_service.py`.)

- [ ] **Step 5: Run test to verify it passes**

Run: `… python -m pytest tests/test_bland_triggered_at.py -v`
Expected: PASS (both tests)

- [ ] **Step 6: Commit**

```bash
git add migrations/021_lead_contacts_bland_triggered_at.sql services/dedup_service.py tests/test_bland_triggered_at.py
git commit -m "feat(ops): lead_contacts.bland_triggered_at stamped on fire (guarded)"
```

---

## Task 2: Last-known Rentometer credits (cache kv + capture)

**Files:**
- Modify: `services/enrichment_cache.py` (`_init_db`, new `set_ops_value`/`get_ops_value`)
- Modify: `scripts/backfill_rent.py` (`rentometer_median`)
- Test: `tests/test_ops_kv.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ops_kv.py
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
    assert isinstance(updated_at, str) and updated_at      # ISO timestamp
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ops_kv.py -v`
Expected: FAIL — `get_ops_value` not defined.

- [ ] **Step 3: Implement the kv store**

In `services/enrichment_cache.py` `_init_db`, after the `alert_dedupe` table creation, add:

```python
            con.execute("""
                CREATE TABLE IF NOT EXISTS ops_kv (
                    key        TEXT PRIMARY KEY,
                    value      TEXT,
                    updated_at TEXT NOT NULL
                )
            """)
```

Add these methods to `EnrichmentCache` (near `claim_alert_once_today`):

```python
    def set_ops_value(self, key: str, value: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self._db_path) as con:
            con.execute(
                "INSERT INTO ops_kv (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, value, now),
            )

    def get_ops_value(self, key: str) -> str | None:
        with sqlite3.connect(self._db_path) as con:
            row = con.execute("SELECT value FROM ops_kv WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def get_ops_value_with_ts(self, key: str) -> tuple[str | None, str | None]:
        with sqlite3.connect(self._db_path) as con:
            row = con.execute("SELECT value, updated_at FROM ops_kv WHERE key=?", (key,)).fetchone()
        return (row[0], row[1]) if row else (None, None)

    def daily_count(self, kind: str = "searchbug") -> int:
        """Today's counter value for `kind` (0 if none). Used by the ops dashboard
        spend strip; check_daily_cap stays the boolean gate."""
        today = date.today().isoformat()
        with sqlite3.connect(self._db_path) as con:
            row = con.execute("SELECT count FROM daily_cap WHERE date=? AND kind=?", (today, kind)).fetchone()
        return row[0] if row else 0
```

Add a quick assertion to `tests/test_ops_kv.py`:

```python
def test_daily_count_reads_counter(tmp_path):
    c = EnrichmentCache(db_path=str(tmp_path / "c.db"))
    assert c.daily_count("bland") == 0
    c.increment_daily_count(kind="bland")
    assert c.daily_count("bland") == 1
```

Add `from datetime import datetime, timezone` to the imports if not present (the file currently imports `from datetime import date` — change to `from datetime import date, datetime, timezone`).

- [ ] **Step 4: Capture credits in the rent path**

In `scripts/backfill_rent.py` `rentometer_median`, where it parses the response (`d = json.loads(resp.read())`), record credits before returning:

```python
        d = json.loads(resp.read())
        credits = d.get("credits_remaining")
        if credits is not None:
            try:
                from services.enrichment_cache import get_cache
                get_cache().set_ops_value("rentometer_credits", str(int(credits)))
            except Exception:
                pass  # never let bookkeeping break a rent lookup
        return None if d.get("error") else d.get("median")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_ops_kv.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add services/enrichment_cache.py scripts/backfill_rent.py tests/test_ops_kv.py
git commit -m "feat(ops): cache last-known Rentometer credits (ops_kv)"
```

---

## Task 3: ops_stats pure helpers

**Files:**
- Create: `services/ops_stats.py`
- Test: `tests/test_ops_stats.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ops_stats.py
from services import ops_stats as ops


def test_pct_drop_stages():
    # stage counts -> each stage's % of the previous (first is None)
    stages = [{"label": "Scraped", "count": 100},
              {"label": "Enrichable", "count": 60},
              {"label": "Rent>=1600", "count": 30}]
    out = ops.with_pct(stages)
    assert out[0]["pct"] is None
    assert out[1]["pct"] == 60
    assert out[2]["pct"] == 50


def test_with_pct_handles_zero_previous():
    out = ops.with_pct([{"label": "a", "count": 0}, {"label": "b", "count": 0}])
    assert out[1]["pct"] is None      # no divide-by-zero


def test_sparkline_maps_to_blocks():
    # min -> lowest block, max -> highest block, empty -> ''
    assert ops.sparkline([]) == ""
    s = ops.sparkline([0, 5, 10])
    assert len(s) == 3
    assert s[0] != s[2]               # 0 and 10 render differently
    assert ops.sparkline([4, 4, 4]) == "▄▄▄"   # all-equal -> mid block
```

- [ ] **Step 2: Run test to verify it fails**

Run: `… python -m pytest tests/test_ops_stats.py -v`
Expected: FAIL — `No module named 'services.ops_stats'`

- [ ] **Step 3: Implement the helpers**

```python
# services/ops_stats.py
"""Aggregations for the /ops dashboard. Section functions are fault-isolated by
get_ops_stats so one broken query never blanks the page."""
from __future__ import annotations

_BLOCKS = "▁▂▃▄▅▆▇█"


def with_pct(stages: list[dict]) -> list[dict]:
    """Annotate each stage with pct = count/prev_count*100 (rounded). First is None;
    a zero previous yields None (no divide-by-zero)."""
    out = []
    for i, s in enumerate(stages):
        if i == 0:
            pct = None
        else:
            prev = stages[i - 1]["count"]
            pct = round(s["count"] / prev * 100) if prev else None
        out.append({**s, "pct": pct})
    return out


def sparkline(values: list[float]) -> str:
    """Unicode sparkline. Empty -> ''. All-equal -> mid blocks."""
    if not values:
        return ""
    lo, hi = min(values), max(values)
    if hi == lo:
        return _BLOCKS[len(_BLOCKS) // 2] * len(values)
    span = hi - lo
    return "".join(_BLOCKS[int((v - lo) / span * (len(_BLOCKS) - 1))] for v in values)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `… python -m pytest tests/test_ops_stats.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/ops_stats.py tests/test_ops_stats.py
git commit -m "feat(ops): ops_stats pure helpers (with_pct, sparkline)"
```

---

## Task 4: Funnel section (Vantage | ISTS)

**Files:**
- Modify: `services/ops_stats.py` (add `funnel`)
- Test: `tests/test_ops_stats.py` (add fake Supabase + funnel tests)

- [ ] **Step 1: Add the fake Supabase + failing test**

Add to the TOP of `tests/test_ops_stats.py` (reused by later tasks):

```python
from datetime import date, timedelta


class _Result:
    def __init__(self, data): self.data = data


class _Query:
    """Filters are no-ops; the test supplies exactly the rows a query should return
    for the given table. paginate()/range() returns all rows once."""
    def __init__(self, rows): self._rows = rows; self._sliced = False
    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def gte(self, *a, **k): return self
    def lte(self, *a, **k): return self
    def lt(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def order(self, *a, **k): return self
    @property
    def not_(self): return self
    def is_(self, *a, **k): return self
    def range(self, lo, hi):
        # serve all rows on the first page, empty after (stops pagination loops)
        self._sliced = not self._sliced
        return self
    def limit(self, *a, **k): return self
    def execute(self):
        return _Result(self._rows if self._sliced else [])


class FakeSB:
    def __init__(self, tables): self.tables = tables
    def table(self, name): return _Query(list(self.tables.get(name, [])))


def test_funnel_counts_and_pct_drop():
    today = date(2026, 6, 15)
    filings = [
        {"case_number": "V1", "is_enrichable": True, "estimated_rent": 2000},
        {"case_number": "V2", "is_enrichable": True, "estimated_rent": 1700},
        {"case_number": "V3", "is_enrichable": False, "estimated_rent": 2500},  # not enrichable
        {"case_number": "V4", "is_enrichable": True, "estimated_rent": 1200},   # <1600
    ]
    lead_contacts = [
        {"case_number": "V1", "phone": "1", "dnc_status": "callable",
         "bland_call_id": "b1", "ghl_contact_id": "g1"},
        {"case_number": "V2", "phone": "2", "dnc_status": "dnc",
         "bland_call_id": None, "ghl_contact_id": "g2"},
    ]
    sb = FakeSB({"filings": filings, "lead_contacts": lead_contacts, "ists_judgments": []})
    f = ops.funnel(sb, today=today)["vantage"]
    by = {s["label"]: s["count"] for s in f}
    assert by["Scraped"] == 4
    assert by["Enrichable"] == 3          # V1,V2,V4
    assert by["Rent >= $1600"] == 2       # V1,V2 (enrichable & >=1600)
    assert by["Phone found"] == 2         # V1,V2 have phones
    assert by["Callable"] == 1            # only V1 callable
    assert by["Fired"] == 1               # only V1 has bland_call_id
    assert by["Staged to GHL"] == 2       # V1,V2 have ghl ids
    # pct annotated
    assert next(s for s in f if s["label"] == "Enrichable")["pct"] == 75
```

- [ ] **Step 2: Run test to verify it fails**

Run: `… python -m pytest tests/test_ops_stats.py::test_funnel_counts_and_pct_drop -v`
Expected: FAIL — `funnel` not defined.

- [ ] **Step 3: Implement `funnel`**

Add to `services/ops_stats.py`:

```python
from datetime import date, timedelta


def _paginate(sb, table, select, build=lambda q: q):
    rows, off = [], 0
    while True:
        b = build(sb.table(table).select(select)).order("case_number").range(off, off + 999).execute().data or []
        rows += b
        if len(b) < 1000:
            break
        off += 1000
    return rows


def funnel(sb, *, today: date | None = None) -> dict:
    today = today or date.today()
    w21 = (today - timedelta(days=21)).isoformat()
    w14 = (today - timedelta(days=14)).isoformat()

    # --- Vantage ---
    fil = _paginate(sb, "filings",
                    "case_number,is_enrichable,estimated_rent,filing_date,court_date",
                    lambda q: q.gte("filing_date", w21))
    enrich = [r for r in fil if r.get("is_enrichable")]
    ge1600 = [r for r in enrich if r.get("estimated_rent") and float(r["estimated_rent"]) >= 1600]
    ge_cns = {r["case_number"] for r in ge1600}
    lc = _paginate(sb, "lead_contacts",
                   "case_number,phone,dnc_status,bland_call_id,ghl_contact_id",
                   lambda q: q.eq("track", "ng"))
    lc_ge = [r for r in lc if r["case_number"] in ge_cns]
    phoned = [r for r in lc_ge if r.get("phone")]
    callable_ = [r for r in phoned if r.get("dnc_status") == "callable"]
    fired = [r for r in lc_ge if r.get("bland_call_id")]
    staged = [r for r in lc_ge if r.get("ghl_contact_id")]
    vantage = with_pct([
        {"label": "Scraped", "count": len(fil)},
        {"label": "Enrichable", "count": len(enrich)},
        {"label": "Rent >= $1600", "count": len(ge1600)},
        {"label": "Phone found", "count": len(phoned)},
        {"label": "Callable", "count": len(callable_)},
        {"label": "Fired", "count": len(fired)},
        {"label": "Staged to GHL", "count": len(staged)},
    ])

    # --- ISTS ---
    j = _paginate(sb, "ists_judgments",
                  "case_number,judgment_date,estimated_rent,phone,dnc_status,bland_call_id,ghl_contact_id")
    fresh = [r for r in j if (r.get("judgment_date") or "") >= w14]
    jge = [r for r in fresh if r.get("estimated_rent") and float(r["estimated_rent"]) >= 1600]
    jphone = [r for r in jge if r.get("phone")]
    jcall = [r for r in jphone if r.get("dnc_status") == "callable"]
    jfired = [r for r in jge if r.get("bland_call_id")]
    jstaged = [r for r in jge if r.get("ghl_contact_id")]
    ists = with_pct([
        {"label": "Scraped", "count": len(j)},
        {"label": "Fresh (14d)", "count": len(fresh)},
        {"label": "Rent >= $1600", "count": len(jge)},
        {"label": "Phone found", "count": len(jphone)},
        {"label": "Callable", "count": len(jcall)},
        {"label": "Fired", "count": len(jfired)},
        {"label": "Staged to GHL", "count": len(jstaged)},
    ])
    return {"vantage": vantage, "ists": ists}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `… python -m pytest tests/test_ops_stats.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/ops_stats.py tests/test_ops_stats.py
git commit -m "feat(ops): funnel section (Vantage|ISTS) with pct-drop"
```

---

## Task 5: scrapes, spend, health_flags, trend + get_ops_stats composer

**Files:**
- Modify: `services/ops_stats.py`
- Test: `tests/test_ops_stats.py`

- [ ] **Step 1: Write failing tests**

```python
# add to tests/test_ops_stats.py
class _FakeCache:
    def __init__(self, counts, ops_kv):
        self._counts = counts; self._kv = ops_kv
    def check_daily_cap(self, cap, kind="searchbug"): return self._counts.get(kind, 0) < cap
    def daily_count(self, kind="searchbug"): return self._counts.get(kind, 0)
    def get_ops_value_with_ts(self, key): return self._kv.get(key, (None, None))


def test_spend_reports_today_counts_and_caps(monkeypatch):
    cache = _FakeCache({"searchbug": 73, "bland": 99}, {"rentometer_credits": ("263", "2026-06-14T10:00:00Z")})
    monkeypatch.setenv("SEARCHBUG_DAILY_CAP", "100")
    monkeypatch.setenv("BLAND_DAILY_CAP", "100")
    s = ops.spend(cache)
    assert s["bland_today"] == 99 and s["bland_cap"] == 100
    assert s["searchbug_today"] == 73
    assert s["rentometer_credits"] == 263


def test_health_flags_dark_scraper(monkeypatch):
    today = date(2026, 6, 15)
    # Harris scraped today; Hamilton last scraped 29 days ago -> dark
    filings = [{"county": "Harris", "scraped_at": "2026-06-15T13:00:00Z", "enrichable_checked_at": "2026-06-15T15:00:00Z"},
               {"county": "Hamilton", "scraped_at": "2026-05-17T13:00:00Z", "enrichable_checked_at": None}]
    sb = FakeSB({"filings": filings})
    cache = _FakeCache({"bland": 0}, {})
    flags = ops.health_flags(sb, cache, today=today)["flags"]
    msgs = " ".join(f["msg"] for f in flags)
    assert "Hamilton" in msgs and "dark" in msgs.lower()


def test_get_ops_stats_isolates_section_errors(monkeypatch):
    # funnel raises; other sections still populate
    monkeypatch.setattr(ops, "funnel", lambda sb, today=None: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(ops, "scrapes", lambda sb, today=None: {"rows": []})
    monkeypatch.setattr(ops, "spend", lambda cache: {"bland_today": 0})
    monkeypatch.setattr(ops, "health_flags", lambda sb, cache, today=None: {"flags": []})
    monkeypatch.setattr(ops, "trend", lambda sb, today=None: {"filings": []})
    out = ops.get_ops_stats(FakeSB({}), _FakeCache({}, {}))
    assert out["funnel"]["error"]              # captured, not raised
    assert out["scrapes"] == {"rows": []}      # others intact
    assert "as_of" in out
```

- [ ] **Step 2: Run to verify they fail**

Run: `… python -m pytest tests/test_ops_stats.py -k "spend or health_flags or isolates" -v`
Expected: FAIL — functions not defined.

- [ ] **Step 3: Implement the sections + composer**

Add to `services/ops_stats.py`:

```python
import os
from datetime import datetime, timezone

# scheduled counties expected to scrape (from services/daily_scheduler.py)
_EXPECTED_COUNTIES = ("Harris", "Davidson", "Franklin", "Maricopa", "Hamilton")


def scrapes(sb, *, today: date | None = None) -> dict:
    today = today or date.today()
    lo = today.isoformat()
    rm = _paginate_rm(sb, gte=(today - timedelta(days=7)).isoformat())
    by_county_today: dict[str, dict] = {}
    spark: dict[str, list] = {}
    for r in rm:
        c = (r.get("county") or "").replace(" County", "")
        spark.setdefault(c, []).append(r)
        if (r.get("run_at") or "") >= lo:
            by_county_today[c] = r
    rows = []
    for c in _EXPECTED_COUNTIES:
        t = by_county_today.get(c)
        hist = sorted(spark.get(c, []), key=lambda x: x.get("run_at") or "")
        rows.append({
            "county": c,
            "received": (t or {}).get("filings_received"),
            "new": ((t or {}).get("filings_received", 0) - (t or {}).get("duplicates_skipped", 0)) if t else None,
            "dupes": (t or {}).get("duplicates_skipped"),
            "last_run": ((t or {}).get("run_at") or "")[11:16] if t else None,
            "missing": t is None,
            "spark7": sparkline([h.get("filings_received", 0) for h in hist]),
        })
    return {"rows": rows}


def _paginate_rm(sb, gte: str) -> list:
    rows, off = [], 0
    while True:
        b = (sb.table("run_metrics").select("county,run_at,filings_received,duplicates_skipped,phones_found")
             .gte("run_at", gte).order("run_at").range(off, off + 999).execute().data or [])
        rows += b
        if len(b) < 1000:
            break
        off += 1000
    return rows


def spend(cache) -> dict:
    cred, cred_ts = cache.get_ops_value_with_ts("rentometer_credits")
    return {
        "searchbug_today": cache.daily_count("searchbug"),
        "searchbug_cap": int(os.getenv("SEARCHBUG_DAILY_CAP", "100")),
        "bland_today": cache.daily_count("bland"),
        "bland_cap": int(os.getenv("BLAND_DAILY_CAP", "100")),
        "rentometer_credits": int(cred) if cred is not None else None,
        "rentometer_as_of": cred_ts,
    }


def health_flags(sb, cache, *, today: date | None = None) -> dict:
    today = today or date.today()
    flags: list[dict] = []
    fil = _paginate(sb, "filings", "county,scraped_at,enrichable_checked_at")
    last_scrape: dict[str, str] = {}
    last_checked = ""
    for r in fil:
        c = (r.get("county") or "").replace(" County", "")
        sa = r.get("scraped_at") or ""
        if sa > last_scrape.get(c, ""):
            last_scrape[c] = sa
        ca = r.get("enrichable_checked_at") or ""
        if ca > last_checked:
            last_checked = ca
    cutoff = (today - timedelta(days=7)).isoformat()
    for c in _EXPECTED_COUNTIES:
        ls = last_scrape.get(c, "")
        if ls and ls[:10] < cutoff:
            days = (today - date.fromisoformat(ls[:10])).days
            flags.append({"level": "red", "msg": f"{c}: dark — no filings in {days}d"})
    if last_checked[:10] != today.isoformat():
        flags.append({"level": "warn", "msg": "post-scrape chain hasn't run today"})
    if not cache.check_daily_cap(int(os.getenv("BLAND_DAILY_CAP", "100")), kind="bland"):
        flags.append({"level": "warn", "msg": "Bland at daily cap"})
    if not os.getenv("DNCSCRUB_LOGIN_ID", "").strip():
        flags.append({"level": "warn", "msg": "DNCScrub not configured (local-files only)"})
    if not flags:
        flags.append({"level": "ok", "msg": "All systems nominal"})
    return {"flags": flags}


def trend(sb, *, today: date | None = None) -> dict:
    today = today or date.today()
    days = [(today - timedelta(days=n)).isoformat() for n in range(6, -1, -1)]
    rm = _paginate_rm(sb, gte=days[0])
    filings = {d: 0 for d in days}
    phones = {d: 0 for d in days}
    for r in rm:
        d = (r.get("run_at") or "")[:10]
        if d in filings:
            filings[d] += r.get("filings_received") or 0
            phones[d] += r.get("phones_found") or 0
    fired = {d: 0 for d in days}
    for table, col in (("lead_contacts", "bland_triggered_at"), ("ists_judgments", "bland_triggered_at")):
        for r in _paginate(sb, table, f"case_number,{col}", lambda q: q.gte(col, days[0])):
            d = (r.get(col) or "")[:10]
            if d in fired:
                fired[d] += 1
    return {"filings": [filings[d] for d in days], "phones": [phones[d] for d in days],
            "fired": [fired[d] for d in days], "days": days}


def get_ops_stats(sb, cache, *, today: date | None = None) -> dict:
    today = today or date.today()
    out = {"as_of": datetime.now(timezone.utc).isoformat()}
    sections = {
        "health": lambda: health_flags(sb, cache, today=today),
        "scrapes": lambda: scrapes(sb, today=today),
        "spend": lambda: spend(cache),
        "funnel": lambda: funnel(sb, today=today),
        "trend": lambda: trend(sb, today=today),
    }
    for name, fn in sections.items():
        try:
            out[name] = fn()
        except Exception as e:
            out[name] = {"error": repr(e)[:160]}
    return out
```

- [ ] **Step 4: Run to verify they pass**

Run: `… python -m pytest tests/test_ops_stats.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add services/ops_stats.py tests/test_ops_stats.py
git commit -m "feat(ops): scrapes/spend/health/trend sections + fault-isolated composer"
```

---

## Task 6: Dashboard routes `/ops` + `/api/ops`

**Files:**
- Modify: `dashboard/main.py`
- Test: `tests/test_ops_route.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ops_route.py
from unittest.mock import patch
from fastapi.testclient import TestClient

from dashboard import main as dash
from dashboard.auth import require_queue


def test_api_ops_returns_sections():
    fake = {"as_of": "t", "health": {"flags": []}, "scrapes": {"rows": []},
            "spend": {}, "funnel": {"vantage": [], "ists": []}, "trend": {}}
    dash.app.dependency_overrides[require_queue] = lambda: None   # bypass auth deterministically
    try:
        with patch("services.ops_stats.get_ops_stats", return_value=fake):
            client = TestClient(dash.app)
            r = client.get("/api/ops")
    finally:
        dash.app.dependency_overrides.pop(require_queue, None)
    assert r.status_code == 200
    assert r.json()["health"] == {"flags": []}
```

> Note for implementer: `dependency_overrides` is FastAPI's built-in test seam and works regardless of how `require_queue` authenticates — no need to know the auth internals. If `tests/test_dashboard_*` already establish a different convention, match it.

- [ ] **Step 2: Run to verify it fails**

Run: `… python -m pytest tests/test_ops_route.py -v`
Expected: FAIL — 404 (route not defined).

- [ ] **Step 3: Add the routes**

In `dashboard/main.py`, near the `_LISTS_HTML` definition add:

```python
_OPS_HTML = Path(__file__).parent / "ops.html"
```

And near the `/lists` + `/api/queue` routes add:

```python
@app.get("/ops", response_class=FileResponse, dependencies=[Depends(require_queue)])
async def dashboard_ops():
    """Ops dashboard: scrape health + funnel + spend, one page."""
    return FileResponse(_OPS_HTML)


@app.get("/api/ops", dependencies=[Depends(require_queue)])
async def api_ops():
    from services.dedup_service import _client as sb
    from services.enrichment_cache import get_cache
    from services import ops_stats
    data = await asyncio.to_thread(ops_stats.get_ops_stats, sb, get_cache())
    return JSONResponse(data)
```

(`asyncio`, `Depends`, `FileResponse`, `JSONResponse`, `require_queue` are already imported in `main.py`.)

- [ ] **Step 4: Run to verify it passes**

Run: `… python -m pytest tests/test_ops_route.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/main.py tests/test_ops_route.py
git commit -m "feat(ops): /ops + /api/ops routes"
```

---

## Task 7: `ops.html` render + live verification

**Files:**
- Create: `dashboard/ops.html`

- [ ] **Step 1: Write the page**

Create `dashboard/ops.html` — dark theme matching `lists.html`/`search.html`, fetches `/api/ops`, renders: health chips, scrapes table (with `spark7`), spend strip, two funnel columns (Vantage|ISTS with count + pct), and the 7-day sparklines (filings/phones/fired). A `Refresh` button re-calls `load()`. Each section checks `data[name].error` and renders an "unavailable" note if set. Use the exact JSON keys from Task 5 (`health.flags[].level/msg`, `scrapes.rows[].county/received/new/dupes/last_run/missing/spark7`, `spend.searchbug_today/searchbug_cap/bland_today/bland_cap/rentometer_credits/rentometer_as_of`, `funnel.vantage[]/ists[]` each `{label,count,pct}`, `trend.filings/phones/fired/days`).

```html
<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Ops · Leadgen</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root{--bg:#0d1117;--panel:#161b22;--line:rgba(255,255,255,.08);--txt:#e6edf3;
        --dim:rgba(230,237,243,.6);--accent:#6cb0ff;--green:#6ee7b7;--red:#ff9a9a;--orange:#ffb066;}
  *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--txt);
    font:14px system-ui,Segoe UI,Roboto,sans-serif}
  header{display:flex;align-items:center;gap:14px;padding:12px 20px;border-bottom:1px solid var(--line);background:var(--panel)}
  h1{font-size:16px;margin:0}.spacer{flex:1}
  button{background:var(--panel);color:var(--txt);border:1px solid var(--line);border-radius:6px;padding:6px 12px;cursor:pointer}
  .wrap{padding:16px 20px;max-width:1100px}
  .sec{margin:18px 0}.sec h2{font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--dim);margin:0 0 8px}
  .chips{display:flex;flex-wrap:wrap;gap:8px}
  .chip{padding:3px 10px;border-radius:20px;font-size:12px;font-weight:600}
  .chip.red{background:rgba(255,120,120,.16);color:var(--red)} .chip.warn{background:rgba(255,176,102,.16);color:var(--orange)}
  .chip.ok{background:rgba(110,231,183,.15);color:var(--green)}
  table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:6px 10px;border-bottom:1px solid var(--line);font-size:13px}
  th{color:var(--dim);font-size:11px;text-transform:uppercase}
  .spark{font-family:ui-monospace,monospace;letter-spacing:1px}
  .miss{color:var(--red)} .strip{display:flex;gap:24px;font-size:14px}
  .funnels{display:flex;gap:32px}.funnels>div{flex:1}
  .frow{display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid var(--line)}
  .pct{color:var(--dim);font-size:12px} .err{color:var(--orange);font-size:12px}
</style></head><body>
<header><h1>Pipeline Ops</h1><span class="spacer"></span>
  <span id="asof" class="pct"></span><button onclick="load()">Refresh</button></header>
<div class="wrap" id="root">Loading…</div>
<script>
const esc=s=>String(s==null?'':s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
function funnelCol(title,stages){
  if(!stages) return `<div><h2>${title}</h2><div class="err">unavailable</div></div>`;
  return `<div><h2>${title}</h2>`+stages.map(s=>
    `<div class="frow"><span>${esc(s.label)}</span><span>${s.count}${s.pct!=null?` <span class="pct">${s.pct}%</span>`:''}</span></div>`).join('')+`</div>`;
}
async function load(){
  const root=document.getElementById('root');
  let d; try{ const r=await fetch('/api/ops'); d=await r.json(); }catch(e){ root.textContent='Load failed: '+e; return; }
  document.getElementById('asof').textContent='as of '+(d.as_of||'').slice(0,19).replace('T',' ');
  const H=d.health, S=d.scrapes, SP=d.spend, F=d.funnel, T=d.trend;
  const health = H&&!H.error ? `<div class="chips">`+H.flags.map(f=>`<span class="chip ${f.level}">${esc(f.msg)}</span>`).join('')+`</div>` : `<div class="err">unavailable</div>`;
  const scrapes = S&&!S.error ? `<table><tr><th>County</th><th>Recv</th><th>New</th><th>Dupes</th><th>Last run</th><th>7-day</th></tr>`+
    S.rows.map(r=>`<tr><td>${esc(r.county)}</td>`+(r.missing?`<td colspan=4 class="miss">missing today</td>`:
      `<td>${r.received}</td><td>${r.new}</td><td>${r.dupes}</td><td>${esc(r.last_run)}</td>`)+
      `<td class="spark">${esc(r.spark7)}</td></tr>`).join('')+`</table>` : `<div class="err">unavailable</div>`;
  const spend = SP&&!SP.error ? `<div class="strip">
      <span>SearchBug <b>${SP.searchbug_today}/${SP.searchbug_cap}</b></span>
      <span>Bland <b>${SP.bland_today}/${SP.bland_cap}</b></span>
      <span>Rentometer <b>${SP.rentometer_credits??'?'}</b> left${SP.rentometer_as_of?` <span class="pct">(as of ${esc((SP.rentometer_as_of||'').slice(0,10))})</span>`:''}</span>
    </div>` : `<div class="err">unavailable</div>`;
  const funnel = F&&!F.error ? `<div class="funnels">${funnelCol('Vantage',F.vantage)}${funnelCol('ISTS',F.ists)}</div>` : `<div class="err">unavailable</div>`;
  const trend = T&&!T.error ? `<div class="strip">
      <span>filings <span class="spark">${spark(T.filings)}</span></span>
      <span>phones <span class="spark">${spark(T.phones)}</span></span>
      <span>fired <span class="spark">${spark(T.fired)}</span></span></div>` : `<div class="err">unavailable</div>`;
  root.innerHTML=`
    <div class="sec"><h2>Health</h2>${health}</div>
    <div class="sec"><h2>Today's scrapes</h2>${scrapes}</div>
    <div class="sec"><h2>Spend &amp; caps</h2>${spend}</div>
    <div class="sec"><h2>Funnel</h2>${funnel}</div>
    <div class="sec"><h2>7-day trend</h2>${trend}</div>`;
}
const BLK='▁▂▃▄▅▆▇█';
function spark(v){ if(!v||!v.length) return ''; const lo=Math.min(...v),hi=Math.max(...v);
  if(hi===lo) return BLK[4].repeat(v.length);
  return v.map(x=>BLK[Math.round((x-lo)/(hi-lo)*7)]).join(''); }
load();
</script></body></html>
```

- [ ] **Step 2: Verify the JSON shape end-to-end (offline)**

Run: `SUPABASE_URL=… SUPABASE_SERVICE_ROLE_KEY=… python -c "import json,sys; sys.path.insert(0,'.'); from tests.test_ops_stats import FakeSB, _FakeCache; from services import ops_stats; print(json.dumps(ops_stats.get_ops_stats(FakeSB({}), _FakeCache({},{})), default=str)[:400])"`
Expected: a JSON object with `as_of`, `health`, `scrapes`, `spend`, `funnel`, `trend` keys (sections may be empty/zero with the empty fake — that's fine; you're checking the shape the page consumes).

- [ ] **Step 3: Live render check (real data)**

Run the dashboard locally against the real DB and open `/ops`:
```bash
# from the repo with .env present:
sh scripts/start_dashboard.sh   # or: uvicorn dashboard.main:app --port 8000
```
Open `http://localhost:8000/ops` (use the dashboard auth). Confirm: health chips render, scrapes table shows today's counties with sparklines, spend strip shows SearchBug/Bland/Rentometer, both funnel columns populate, trend sparklines show. Capture a screenshot.

- [ ] **Step 4: Commit**

```bash
git add dashboard/ops.html
git commit -m "feat(ops): ops.html dashboard page"
```

---

## Final verification

- [ ] Run the new suites together:

```bash
SUPABASE_URL=https://test.supabase.co SUPABASE_SERVICE_ROLE_KEY=test-key python -m pytest \
  tests/test_ops_stats.py tests/test_ops_kv.py tests/test_bland_triggered_at.py tests/test_ops_route.py \
  tests/test_enrichment_cache_kinds.py tests/test_searchbug_circuit_breaker.py -q
```
Expected: all PASS (the last two confirm the cache changes didn't regress the existing daily-cap/cache behavior).

- [ ] Confirm no new failures vs the pre-existing baseline (network/runner tests).
- [ ] **Operator action:** apply `migrations/021_lead_contacts_bland_triggered_at.sql` to the live DB (additive/nullable; the fired/day trend stays empty for the NG track until applied — ISTS fires show immediately since `ists_judgments.bland_triggered_at` already exists).

## Coverage check (plan ↔ spec)

| Spec item | Task |
|---|---|
| Health flags | Task 5 (`health_flags`) |
| Per-county scrapes + 7d spark | Task 5 (`scrapes`) + Task 3 (`sparkline`) |
| Spend & caps (incl. last-known Rentometer) | Task 2 (capture) + Task 5 (`spend`) |
| Per-track funnel + %-drop | Task 4 (`funnel`) + Task 3 (`with_pct`) |
| 7-day trend (filings/phones/fired) | Task 5 (`trend`) + Task 1 (`bland_triggered_at`) |
| `/ops` + `/api/ops` behind auth | Task 6 |
| `ops.html` render + fault isolation UI | Task 7 + Task 5 (`get_ops_stats`) |
| Per-section fault isolation | Task 5 (`get_ops_stats`) |

## Out of scope (per spec)
Per-county funnel, auto-refresh, chart libraries, historical storage beyond `run_metrics`.
