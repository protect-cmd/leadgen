# Unified Caller-Lookup Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a search-first dashboard at `/` so a live caller can resolve any lead in one keystroke flow, with light actions (mark called, add note, skip) from a slide-in detail panel.

**Architecture:** New `search.html` page served from `/`; existing queue UI moves to `/queue`. Live typeahead hits `GET /api/search` which queries `lead_contacts` LEFT JOIN `filings`, merging substring matches across name / phone / case# / address. Detail-panel actions write to a new `lead_notes` table and a new `lead_contacts.last_called_at` column.

**Tech Stack:** FastAPI (existing dashboard service), Supabase (Postgres + REST), vanilla HTML/JS for the frontend, pytest + pytest-asyncio for tests. No new dependencies.

**Spec reference:** [docs/superpowers/specs/2026-05-29-unified-caller-dashboard-design.md](../specs/2026-05-29-unified-caller-dashboard-design.md)

---

## File Structure

**To create:**
- `migrations/014_lead_notes_and_last_called.sql` — schema additions
- `dashboard/search.html` — search-first frontend (vanilla HTML/JS, ~450 lines)
- `tests/test_lead_search_helpers.py` — service-layer tests (search, notes, mark_called)
- `tests/test_dashboard_search_routes.py` — route + endpoint tests

**To modify:**
- `services/dedup_service.py` — add `search_leads`, `add_lead_note`, `list_lead_notes`, `mark_lead_called`
- `dashboard/main.py` — swap `/` to serve `search.html`, add `/queue` for the old UI, add 3 new endpoints

**Why these boundaries:** the four async helpers all touch Supabase and live with the rest of dedup_service. The route changes and endpoint handlers live with the rest of `dashboard/main.py` (matches the existing pattern of 7 endpoints in one file). The frontend is one focused HTML file matching `dashboard/index.html`'s pattern.

---

## Task 1: Migration 014 — `lead_notes` table + `last_called_at` column

**Files:**
- Create: `migrations/014_lead_notes_and_last_called.sql`

No tests — schema files are applied via Supabase SQL Editor and verified after.

- [ ] **Step 1: Write the migration SQL**

Create `migrations/014_lead_notes_and_last_called.sql`:

```sql
-- 014_lead_notes_and_last_called.sql
--
-- Adds support for the unified caller-lookup dashboard (Spec 4):
--   1. lead_notes — append-only timestamped notes per (case_number, track)
--   2. lead_contacts.last_called_at — single timestamp for the most-recent
--      outbound call, shown in the detail panel as "Last called: 2h ago"
--
-- Both additions are nullable / append-only — safe to apply on a live system.

BEGIN;

CREATE TABLE IF NOT EXISTS lead_notes (
    id           BIGSERIAL PRIMARY KEY,
    case_number  TEXT NOT NULL,
    track        TEXT NOT NULL,
    note_text    TEXT NOT NULL,
    author       TEXT NOT NULL DEFAULT 'caller',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS lead_notes_case_track_idx
    ON lead_notes (case_number, track);

CREATE INDEX IF NOT EXISTS lead_notes_created_at_idx
    ON lead_notes (created_at DESC);

ALTER TABLE lead_contacts
    ADD COLUMN IF NOT EXISTS last_called_at TIMESTAMPTZ;

COMMIT;
```

- [ ] **Step 2: Commit the migration file (not applied yet)**

```bash
cd "d:\Freelance Projects\EvictionCommand\leadgen"
git add migrations/014_lead_notes_and_last_called.sql
git commit -m "migration: 014 lead_notes table + lead_contacts.last_called_at"
```

The migration will be applied to Supabase as an operational step in Task 8.

---

## Task 2: `search_leads()` service helper

**Files:**
- Modify: `services/dedup_service.py` (add `search_leads`)
- Create: `tests/test_lead_search_helpers.py`

The helper queries `lead_contacts` for matches on `contact_name`, `phone`, `case_number` and queries `filings` for matches on `tenant_name`, `property_address`, `case_number`, then merges by `case_number` and returns up to N rows sorted by `filing_date DESC`.

**Sanitization:** strips `%`, `,`, and other PostgREST filter-breaking chars from the query string before building filter expressions.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lead_search_helpers.py`:

```python
"""Tests for the search/notes/mark-called helpers added in Spec 4."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.dedup_service import search_leads, _sanitize_search_query


def test_sanitize_search_query_strips_filter_breakers():
    assert _sanitize_search_query("ma,ria%g") == "mariag"
    assert _sanitize_search_query("  trim  ") == "trim"
    assert _sanitize_search_query("o'brien") == "o'brien"  # apostrophe is fine
    assert _sanitize_search_query("name-with-dash") == "name-with-dash"


def test_sanitize_search_query_handles_empty():
    assert _sanitize_search_query("") == ""
    assert _sanitize_search_query("   ") == ""
    assert _sanitize_search_query(None) == ""  # type: ignore[arg-type]


def _mock_client_returning(rows):
    """Build a mock _client whose chained calls all return `rows`."""
    client = MagicMock()
    chain = client.table.return_value
    for attr in ("select", "or_", "ilike", "order", "limit"):
        chain = getattr(chain, attr).return_value
    chain.execute.return_value = MagicMock(data=rows)
    return client


@pytest.mark.asyncio
async def test_search_leads_returns_empty_on_short_query():
    """Queries under 2 chars return [] without hitting Supabase."""
    with patch("services.dedup_service._client") as mock_client:
        result = await search_leads("a")
    assert result == []
    mock_client.table.assert_not_called()


@pytest.mark.asyncio
async def test_search_leads_strips_unsafe_chars_before_query():
    """%,'\" — PostgREST filter-breakers — must be stripped from q."""
    client = _mock_client_returning([])
    with patch("services.dedup_service._client", client):
        await search_leads("ma,ria%")
    # Inspect that the .or_() call doesn't contain raw commas/% from user input.
    # The wrapping % around the sanitized value is fine (we add those).
    or_args = []
    for call in client.mock_calls:
        if ".or_(" in str(call):
            or_args.append(call)
    # Confirm at least one or_ call was made with sanitized 'maria' substring
    assert any("maria" in str(c) for c in or_args)
    assert not any(",ria" in str(c) for c in or_args), "raw comma leaked"


@pytest.mark.asyncio
async def test_search_leads_merges_and_dedupes_by_case_number():
    """Same case_number appearing in both contact + filing matches returns once."""
    contact_rows = [
        {"case_number": "C-1", "track": "ng", "contact_name": "Maria Garcia",
         "phone": "5551234567", "filings": {"filing_date": "2026-05-29",
         "property_address": "1 Main", "tenant_name": "Maria Garcia",
         "state": "TX", "county": "Harris", "court_date": None}},
    ]
    filing_rows = [
        {"case_number": "C-1", "tenant_name": "Maria Garcia",
         "property_address": "1 Main", "filing_date": "2026-05-29",
         "state": "TX", "county": "Harris", "court_date": None,
         "lead_contacts": []},
        {"case_number": "C-2", "tenant_name": "Maria Lopez",
         "property_address": "2 Main", "filing_date": "2026-05-28",
         "state": "TX", "county": "Harris", "court_date": None,
         "lead_contacts": []},
    ]
    client = MagicMock()
    # First .table('lead_contacts') chain returns contact_rows;
    # second .table('filings') chain returns filing_rows.
    contact_chain = MagicMock()
    contact_chain.select.return_value.or_.return_value.limit.return_value.execute.return_value = MagicMock(data=contact_rows)
    filing_chain = MagicMock()
    filing_chain.select.return_value.or_.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=filing_rows)
    client.table.side_effect = lambda name: contact_chain if name == "lead_contacts" else filing_chain
    with patch("services.dedup_service._client", client):
        result = await search_leads("maria")
    case_numbers = [r["case_number"] for r in result]
    assert case_numbers.count("C-1") == 1, "C-1 should appear once after merge"
    assert "C-2" in case_numbers


@pytest.mark.asyncio
async def test_search_leads_sorts_by_filing_date_desc():
    """More-recent filings should appear first in the merged list."""
    contact_rows = []
    filing_rows = [
        {"case_number": "OLD", "tenant_name": "Maria",
         "property_address": "x", "filing_date": "2026-05-01",
         "state": "TX", "county": "Harris", "court_date": None,
         "lead_contacts": []},
        {"case_number": "NEW", "tenant_name": "Maria",
         "property_address": "x", "filing_date": "2026-05-29",
         "state": "TX", "county": "Harris", "court_date": None,
         "lead_contacts": []},
    ]
    client = MagicMock()
    contact_chain = MagicMock()
    contact_chain.select.return_value.or_.return_value.limit.return_value.execute.return_value = MagicMock(data=contact_rows)
    filing_chain = MagicMock()
    filing_chain.select.return_value.or_.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=filing_rows)
    client.table.side_effect = lambda name: contact_chain if name == "lead_contacts" else filing_chain
    with patch("services.dedup_service._client", client):
        result = await search_leads("maria")
    assert [r["case_number"] for r in result] == ["NEW", "OLD"]


@pytest.mark.asyncio
async def test_search_leads_respects_limit():
    """Returned list never exceeds the limit parameter."""
    filing_rows = [
        {"case_number": f"C-{i}", "tenant_name": "X",
         "property_address": "x", "filing_date": "2026-05-29",
         "state": "TX", "county": "Harris", "court_date": None,
         "lead_contacts": []}
        for i in range(30)
    ]
    client = MagicMock()
    contact_chain = MagicMock()
    contact_chain.select.return_value.or_.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
    filing_chain = MagicMock()
    filing_chain.select.return_value.or_.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=filing_rows)
    client.table.side_effect = lambda name: contact_chain if name == "lead_contacts" else filing_chain
    with patch("services.dedup_service._client", client):
        result = await search_leads("x", limit=10)
    assert len(result) == 10
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd "d:\Freelance Projects\EvictionCommand\leadgen"
python -m pytest tests/test_lead_search_helpers.py -v
```

Expected: ImportError on `search_leads` / `_sanitize_search_query`.

- [ ] **Step 3: Add `_sanitize_search_query` and `search_leads` to dedup_service**

Add to `services/dedup_service.py` (place them near the other public async helpers, e.g. after `update_enrichment`):

```python
import re


_UNSAFE_CHARS_RE = re.compile(r"[%,()\"\\]")


def _sanitize_search_query(q: str | None) -> str:
    """Strip PostgREST filter-breaking chars from user-supplied search input.

    Keeps letters/digits/spaces/hyphens/apostrophes/periods/at-signs.
    Removes %, comma, parens, quotes, and backslashes (used in PostgREST
    filter syntax). Returns empty string on None or whitespace-only input.
    """
    if not q:
        return ""
    cleaned = _UNSAFE_CHARS_RE.sub("", q).strip()
    return cleaned


async def search_leads(q: str, limit: int = 20) -> list[dict]:
    """Unified search across lead_contacts + filings.

    Matches substring on name (contact_name + tenant_name), phone (digits
    of q), case_number, and property_address. Merges results by
    case_number, sorts by filing_date DESC, returns up to `limit` rows.

    Returns [] for queries under 2 characters.
    """
    safe_q = _sanitize_search_query(q)
    if len(safe_q) < 2:
        return []

    digits_q = "".join(c for c in safe_q if c.isdigit())

    def _query() -> list[dict]:
        # 1) Match against lead_contacts (covers contact_name, phone, case_number)
        contact_filters = [
            f"contact_name.ilike.%{safe_q}%",
            f"case_number.ilike.%{safe_q}%",
        ]
        if digits_q:
            contact_filters.append(f"phone.ilike.%{digits_q}%")
        contact_or = ",".join(contact_filters)

        contact_rows = (
            _client.table("lead_contacts")
            .select(
                "case_number,track,contact_name,phone,email,property_type,"
                "estimated_rent,secondary_address,language_hint,"
                "searchbug_status,last_called_at,ghl_contact_id,bland_status,"
                "filings(filing_date,court_date,tenant_name,property_address,"
                "landlord_name,state,county,notice_type,source_url,lead_bucket)"
            )
            .or_(contact_or)
            .limit(limit)
            .execute()
            .data
            or []
        )

        # 2) Match against filings (covers tenant_name, property_address, case_number)
        filing_or = ",".join([
            f"tenant_name.ilike.%{safe_q}%",
            f"property_address.ilike.%{safe_q}%",
            f"case_number.ilike.%{safe_q}%",
        ])
        filing_rows = (
            _client.table("filings")
            .select(
                "case_number,tenant_name,property_address,landlord_name,"
                "filing_date,court_date,state,county,notice_type,source_url,"
                "lead_bucket,"
                "lead_contacts(track,contact_name,phone,email,property_type,"
                "estimated_rent,secondary_address,searchbug_status,"
                "last_called_at,ghl_contact_id,bland_status)"
            )
            .or_(filing_or)
            .order("filing_date", desc=True)
            .limit(limit)
            .execute()
            .data
            or []
        )

        return _merge_search_rows(contact_rows, filing_rows, limit)

    return await asyncio.to_thread(_query)


def _merge_search_rows(contact_rows: list[dict], filing_rows: list[dict], limit: int) -> list[dict]:
    """Flatten contact_rows + filing_rows into a single sorted list, deduped
    by case_number. Each output row has the union of contact + filing fields."""
    by_case: dict[str, dict] = {}

    # Lead-contact-side rows: contact fields are top-level, filing fields nested
    for r in contact_rows:
        f = r.get("filings") or {}
        if isinstance(f, list):
            f = f[0] if f else {}
        merged = {**f, **{k: v for k, v in r.items() if k != "filings"}}
        case_no = merged.get("case_number")
        if case_no:
            by_case[case_no] = merged

    # Filing-side rows: filing fields top-level, contact fields nested
    for r in filing_rows:
        lcs = r.get("lead_contacts") or []
        if isinstance(lcs, dict):
            lcs = [lcs]
        # Pick the NG contact if present, else first
        chosen = next((c for c in lcs if c.get("track") == "ng"), lcs[0] if lcs else {})
        merged = {**r, **{k: v for k, v in chosen.items() if k != "case_number"}}
        merged.pop("lead_contacts", None)
        case_no = merged.get("case_number")
        if case_no and case_no not in by_case:
            by_case[case_no] = merged

    # Sort by filing_date DESC. Missing dates go to the end.
    out = list(by_case.values())
    out.sort(key=lambda r: r.get("filing_date") or "", reverse=True)
    return out[:limit]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_lead_search_helpers.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add services/dedup_service.py tests/test_lead_search_helpers.py
git commit -m "feat: search_leads unified search helper (lead_contacts + filings)"
```

---

## Task 3: `add_lead_note` + `list_lead_notes` helpers

**Files:**
- Modify: `services/dedup_service.py`
- Modify: `tests/test_lead_search_helpers.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_lead_search_helpers.py`:

```python
from services.dedup_service import add_lead_note, list_lead_notes


@pytest.mark.asyncio
async def test_add_lead_note_inserts_with_default_author():
    """A note is INSERTed with the caller-default author and the right fields."""
    client = MagicMock()
    insert_chain = client.table.return_value.insert.return_value
    insert_chain.execute.return_value = MagicMock(
        data=[{"id": 7, "case_number": "C-1", "track": "ng",
               "note_text": "Hello", "author": "caller",
               "created_at": "2026-05-29T20:00:00+00:00"}]
    )
    with patch("services.dedup_service._client", client):
        row = await add_lead_note(case_number="C-1", track="ng", text="Hello")
    assert row["id"] == 7
    assert row["author"] == "caller"
    # Confirm INSERT body shape
    call_args = client.table.return_value.insert.call_args.args[0]
    assert call_args["case_number"] == "C-1"
    assert call_args["track"] == "ng"
    assert call_args["note_text"] == "Hello"
    assert call_args["author"] == "caller"


@pytest.mark.asyncio
async def test_add_lead_note_rejects_empty_text():
    """Empty / whitespace-only text raises ValueError before any DB call."""
    with patch("services.dedup_service._client") as mock_client:
        with pytest.raises(ValueError, match="empty"):
            await add_lead_note(case_number="C-1", track="ng", text="   ")
    mock_client.table.assert_not_called()


@pytest.mark.asyncio
async def test_add_lead_note_rejects_oversize_text():
    """Text over 2000 chars raises ValueError."""
    with patch("services.dedup_service._client") as mock_client:
        with pytest.raises(ValueError, match="2000"):
            await add_lead_note(case_number="C-1", track="ng", text="x" * 2001)


@pytest.mark.asyncio
async def test_list_lead_notes_returns_rows_in_desc_order():
    """list_lead_notes selects from lead_notes filtered + ordered DESC."""
    rows = [
        {"id": 3, "note_text": "newest", "created_at": "2026-05-29T20:00:00+00:00"},
        {"id": 2, "note_text": "older", "created_at": "2026-05-28T20:00:00+00:00"},
    ]
    client = MagicMock()
    chain = client.table.return_value.select.return_value.eq.return_value.eq.return_value.order.return_value.limit.return_value
    chain.execute.return_value = MagicMock(data=rows)
    with patch("services.dedup_service._client", client):
        out = await list_lead_notes(case_number="C-1", track="ng")
    assert [r["id"] for r in out] == [3, 2]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_lead_search_helpers.py -v
```

Expected: ImportError on `add_lead_note` / `list_lead_notes`.

- [ ] **Step 3: Add the helpers to dedup_service**

Add to `services/dedup_service.py` (place near `search_leads`):

```python
_MAX_NOTE_CHARS = 2000


async def add_lead_note(*, case_number: str, track: str, text: str,
                        author: str = "caller") -> dict:
    """Append a note for (case_number, track). Returns the inserted row.

    Raises ValueError if text is empty/whitespace or exceeds 2000 chars.
    """
    stripped = (text or "").strip()
    if not stripped:
        raise ValueError("note text is empty")
    if len(stripped) > _MAX_NOTE_CHARS:
        raise ValueError(f"note text exceeds {_MAX_NOTE_CHARS} chars")

    payload = {
        "case_number": case_number,
        "track": track,
        "note_text": stripped,
        "author": author,
    }

    def _insert() -> dict:
        r = _execute_with_retry(
            _client.table("lead_notes").insert(payload),
            "insert lead_note",
        )
        rows = r.data or []
        if not rows:
            raise RuntimeError("INSERT lead_note returned no row")
        return rows[0]

    return await asyncio.to_thread(_insert)


async def list_lead_notes(*, case_number: str, track: str,
                          limit: int = 50) -> list[dict]:
    """Return notes for (case_number, track) sorted by created_at DESC."""
    def _query() -> list[dict]:
        return (
            _client.table("lead_notes")
            .select("id,note_text,author,created_at")
            .eq("case_number", case_number)
            .eq("track", track)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
            .data
            or []
        )

    return await asyncio.to_thread(_query)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_lead_search_helpers.py -v
```

Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add services/dedup_service.py tests/test_lead_search_helpers.py
git commit -m "feat: add_lead_note + list_lead_notes async helpers"
```

---

## Task 4: `mark_lead_called` helper

**Files:**
- Modify: `services/dedup_service.py`
- Modify: `tests/test_lead_search_helpers.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_lead_search_helpers.py`:

```python
from services.dedup_service import mark_lead_called


@pytest.mark.asyncio
async def test_mark_lead_called_updates_timestamp():
    """Sends UPDATE on lead_contacts with last_called_at = now()."""
    client = MagicMock()
    chain = client.table.return_value.update.return_value.eq.return_value.eq.return_value
    chain.execute.return_value = MagicMock(data=[{
        "case_number": "C-1", "track": "ng",
        "last_called_at": "2026-05-29T20:00:00+00:00",
    }])
    with patch("services.dedup_service._client", client):
        ts = await mark_lead_called(case_number="C-1", track="ng")
    assert isinstance(ts, str) and "T" in ts
    update_arg = client.table.return_value.update.call_args.args[0]
    assert "last_called_at" in update_arg
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_lead_search_helpers.py -v
```

Expected: ImportError on `mark_lead_called`.

- [ ] **Step 3: Add the helper**

Add to `services/dedup_service.py`:

```python
async def mark_lead_called(*, case_number: str, track: str) -> str:
    """UPDATE lead_contacts SET last_called_at = now() and return the
    resulting timestamp string. Used by the dashboard 'Mark Called' button."""
    now_iso = datetime.now(timezone.utc).isoformat()

    def _update() -> str:
        _execute_with_retry(
            _client.table("lead_contacts")
            .update({"last_called_at": now_iso})
            .eq("case_number", case_number)
            .eq("track", track),
            "mark lead called",
        )
        return now_iso

    return await asyncio.to_thread(_update)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_lead_search_helpers.py -v
```

Expected: 12 passed.

- [ ] **Step 5: Commit**

```bash
git add services/dedup_service.py tests/test_lead_search_helpers.py
git commit -m "feat: mark_lead_called helper for dashboard Mark Called button"
```

---

## Task 5: New API endpoints + route changes in `dashboard/main.py`

**Files:**
- Modify: `dashboard/main.py`
- Create: `tests/test_dashboard_search_routes.py`

Adds three new endpoints + reroutes `/` and `/queue`. Existing endpoints stay unchanged.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dashboard_search_routes.py`:

```python
"""Tests for routing changes + new search/note/mark-called endpoints."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard.main import app

client = TestClient(app)


def test_root_serves_search_html():
    """GET / returns search.html (status 200, contains the search input id)."""
    r = client.get("/")
    assert r.status_code == 200
    # search.html should contain the search input element
    assert "search-input" in r.text or "search" in r.text.lower()


def test_queue_serves_legacy_index_html():
    """GET /queue serves the original index.html (status 200, has brand chips)."""
    r = client.get("/queue")
    assert r.status_code == 200
    # The legacy UI has brand chips for GRANT / VANTAGE
    assert "VANTAGE" in r.text or "GRANT" in r.text


def test_search_endpoint_returns_results():
    fake_rows = [
        {"case_number": "C-1", "tenant_name": "Maria",
         "property_address": "1 Main", "filing_date": "2026-05-29",
         "state": "TX", "county": "Harris", "phone": "5551234567"},
    ]
    with patch("dashboard.main.search_leads", new_callable=AsyncMock, return_value=fake_rows):
        r = client.get("/api/search?q=maria")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert body[0]["case_number"] == "C-1"


def test_search_endpoint_rejects_missing_q():
    """Missing or short q returns 200 with empty list (not an error)."""
    with patch("dashboard.main.search_leads", new_callable=AsyncMock, return_value=[]):
        r = client.get("/api/search")
    assert r.status_code == 200
    assert r.json() == []


def test_note_endpoint_inserts_note():
    fake_row = {"id": 1, "case_number": "C-1", "track": "ng",
                "note_text": "vm left", "author": "caller",
                "created_at": "2026-05-29T20:00:00+00:00"}
    with patch("dashboard.main.add_lead_note", new_callable=AsyncMock, return_value=fake_row):
        r = client.post("/api/leads/C-1/note?track=ng", json={"text": "vm left"})
    assert r.status_code == 200
    assert r.json()["id"] == 1


def test_note_endpoint_rejects_empty_text():
    """Empty note text returns 400."""
    async def _raise(**_):
        raise ValueError("empty")
    with patch("dashboard.main.add_lead_note", new=_raise):
        r = client.post("/api/leads/C-1/note?track=ng", json={"text": ""})
    assert r.status_code == 400


def test_mark_called_endpoint_returns_timestamp():
    with patch("dashboard.main.mark_lead_called",
               new_callable=AsyncMock,
               return_value="2026-05-29T20:00:00+00:00"):
        r = client.post("/api/leads/C-1/mark-called?track=ng")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "last_called_at" in body
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_dashboard_search_routes.py -v
```

Expected: Multiple failures — `search-input` not in current `index.html`, no `/queue` route, no `/api/search` etc.

- [ ] **Step 3: Modify `dashboard/main.py`**

Find the existing route definitions (around line 99) and replace them. Here is the full change:

First, update the imports at the top of `dashboard/main.py` to include the new helpers:

```python
from services.dedup_service import (
    get_dashboard_counts,
    get_dashboard_leads,
    get_pending_leads,
    get_recent_metrics,
    set_bland_status,
    search_leads,
    add_lead_note,
    list_lead_notes,
    mark_lead_called,
)
```

Then add a constant near `_HTML`:

```python
_HTML = Path(__file__).parent / "index.html"
_SEARCH_HTML = Path(__file__).parent / "search.html"
```

Then change the `/` route and add `/queue`:

```python
@app.get("/", response_class=FileResponse)
async def dashboard_search():
    """Search-first landing page (Spec 4)."""
    return FileResponse(_SEARCH_HTML)


@app.get("/queue", response_class=FileResponse)
async def dashboard_queue():
    """Legacy multi-tab queue UI (moved from / in Spec 4)."""
    return FileResponse(_HTML)
```

Then add the three new API endpoints (place them near the existing `/api/leads` route):

```python
@app.get("/api/search")
async def api_search(q: str = "", limit: int = 20):
    if not q or len(q.strip()) < 2:
        return JSONResponse([])
    rows = await search_leads(q=q, limit=limit)
    return JSONResponse(rows)


@app.post("/api/leads/{case_number}/note")
async def api_add_note(case_number: str, payload: dict, track: str = "ng"):
    text = (payload or {}).get("text", "")
    try:
        row = await add_lead_note(case_number=case_number, track=track, text=text)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return JSONResponse(row)


@app.get("/api/leads/{case_number}/notes")
async def api_list_notes(case_number: str, track: str = "ng"):
    rows = await list_lead_notes(case_number=case_number, track=track)
    return JSONResponse(rows)


@app.post("/api/leads/{case_number}/mark-called")
async def api_mark_called(case_number: str, track: str = "ng"):
    ts = await mark_lead_called(case_number=case_number, track=track)
    return JSONResponse({"status": "ok", "last_called_at": ts})
```

- [ ] **Step 4: Create a placeholder `search.html` so the route-serving test passes**

The full frontend lands in Task 6, but we need a minimal stub for the routing test:

```bash
cd "d:\Freelance Projects\EvictionCommand\leadgen"
# Placeholder so the FileResponse doesn't 404
cat > dashboard/search.html <<'EOF'
<!doctype html>
<html><head><title>Search</title></head>
<body><input id="search-input" placeholder="search…"></body>
</html>
EOF
```

(Task 6 replaces this with the real implementation.)

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_dashboard_search_routes.py -v
```

Expected: 7 passed.

- [ ] **Step 6: Commit**

```bash
git add dashboard/main.py dashboard/search.html tests/test_dashboard_search_routes.py
git commit -m "feat: dashboard routes + endpoints for unified search (Spec 4)"
```

---

## Task 6: Full `dashboard/search.html` frontend

**Files:**
- Modify: `dashboard/search.html` (replace the placeholder from Task 5)

No new tests beyond the smoke routing test from Task 5 — the JS is small enough that browser-driven testing is overkill for V1. After this task, manual verification confirms the UI works end-to-end.

- [ ] **Step 1: Replace `dashboard/search.html` with the real frontend**

Overwrite the placeholder created in Task 5. The full file follows. Match the existing `dashboard/index.html` dark theme — colors and font stack should look at home next to the legacy queue UI.

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Search · Leadgen</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root {
      --bg: #0d1117;
      --bg-elev: #161b22;
      --bg-row-hover: rgba(255,255,255,0.04);
      --bg-row-active: rgba(80,160,255,0.15);
      --border: rgba(255,255,255,0.08);
      --text: #e6edf3;
      --text-dim: rgba(230,237,243,0.6);
      --text-dimmer: rgba(230,237,243,0.4);
      --accent: #6cb0ff;
      --green: #6ee7b7;
      --orange: #ffb066;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    }
    header {
      display: flex;
      align-items: center;
      gap: 16px;
      padding: 12px 20px;
      border-bottom: 1px solid var(--border);
      background: var(--bg-elev);
    }
    header .brand { font-weight: 700; }
    header .title { color: var(--text-dim); }
    header .spacer { flex: 1; }
    header a { color: var(--text-dim); text-decoration: none; font-size: 13px; }
    header a:hover { color: var(--text); }

    .search-bar {
      padding: 16px 20px;
      border-bottom: 1px solid var(--border);
    }
    #search-input {
      width: 100%;
      padding: 12px 16px;
      font-size: 16px;
      background: var(--bg-elev);
      border: 1px solid var(--border);
      border-radius: 8px;
      color: var(--text);
      outline: none;
    }
    #search-input:focus { border-color: var(--accent); }

    .main {
      display: flex;
      height: calc(100vh - 110px);
    }
    .results {
      flex: 1.4;
      overflow-y: auto;
      border-right: 1px solid var(--border);
    }
    .meta {
      padding: 10px 20px;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--text-dimmer);
    }
    .row {
      padding: 14px 20px;
      border-bottom: 1px solid var(--border);
      cursor: pointer;
      display: flex;
      justify-content: space-between;
      gap: 16px;
    }
    .row:hover { background: var(--bg-row-hover); }
    .row.active { background: var(--bg-row-active); }
    .row .name { font-weight: 600; font-size: 15px; }
    .row .addr { font-size: 12px; color: var(--text-dim); margin-top: 4px; }
    .row .meta-line { font-size: 11px; color: var(--text-dimmer); margin-top: 4px; font-family: ui-monospace, monospace; }
    .row .right { text-align: right; }
    .row .phone { font-family: ui-monospace, monospace; font-size: 14px; }
    .row .loc { font-size: 11px; color: var(--text-dimmer); margin-top: 2px; }

    .detail {
      flex: 1;
      overflow-y: auto;
      padding: 20px;
    }
    .detail.empty {
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--text-dimmer);
      font-size: 13px;
    }
    .detail h2 { margin: 0; font-size: 20px; }
    .detail .sub { font-size: 12px; color: var(--text-dim); margin-top: 4px; }
    .detail .phone-large {
      font-family: ui-monospace, monospace;
      font-size: 18px;
      margin-top: 16px;
      cursor: pointer;
      display: inline-block;
    }
    .detail .phone-large:hover { color: var(--accent); }
    .detail .addr-large { font-size: 14px; margin-top: 4px; }
    .detail hr { border: 0; border-top: 1px solid var(--border); margin: 20px 0; }
    .detail .kv { display: flex; gap: 12px; font-size: 12px; margin: 4px 0; }
    .detail .kv .k { color: var(--text-dim); min-width: 90px; }
    .detail .actions { display: flex; gap: 8px; margin-top: 8px; }
    .detail .actions button {
      padding: 6px 12px;
      font-size: 12px;
      background: var(--bg-elev);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 4px;
      cursor: pointer;
    }
    .detail .actions button:hover { border-color: var(--accent); color: var(--accent); }
    .detail .actions button.skip { color: var(--orange); }
    .detail .note-form { margin-top: 12px; display: none; }
    .detail .note-form.open { display: block; }
    .detail .note-form textarea {
      width: 100%;
      min-height: 70px;
      padding: 8px;
      background: var(--bg-elev);
      border: 1px solid var(--border);
      border-radius: 4px;
      color: var(--text);
      font: inherit;
      resize: vertical;
    }
    .detail .notes { margin-top: 20px; }
    .detail .note {
      padding: 8px 0;
      border-top: 1px solid var(--border);
      font-size: 13px;
    }
    .detail .note .when { font-size: 11px; color: var(--text-dimmer); margin-top: 2px; }

    .toast {
      position: fixed;
      bottom: 20px;
      left: 50%;
      transform: translateX(-50%);
      padding: 8px 16px;
      background: var(--bg-elev);
      border: 1px solid var(--border);
      border-radius: 6px;
      font-size: 13px;
      opacity: 0;
      pointer-events: none;
      transition: opacity 0.2s;
    }
    .toast.show { opacity: 1; }
  </style>
</head>
<body>
  <header>
    <span class="brand">Leadgen</span>
    <span class="title">Search</span>
    <span class="spacer"></span>
    <a href="/queue">Queue ↗</a>
  </header>

  <div class="search-bar">
    <input id="search-input" type="text" autofocus
           placeholder="Search by name, phone, case #, or address…">
  </div>

  <div class="main">
    <div class="results" id="results">
      <div class="meta" id="results-meta">Type 2+ characters to search.</div>
    </div>
    <div class="detail empty" id="detail">
      <span>Click a result to see details</span>
    </div>
  </div>

  <div class="toast" id="toast"></div>

<script>
const input = document.getElementById('search-input');
const resultsEl = document.getElementById('results');
const metaEl = document.getElementById('results-meta');
const detailEl = document.getElementById('detail');
const toastEl = document.getElementById('toast');

let abortController = null;
let activeRowIndex = -1;
let currentResults = [];
let selectedCase = null;

function showToast(msg) {
  toastEl.textContent = msg;
  toastEl.classList.add('show');
  setTimeout(() => toastEl.classList.remove('show'), 1500);
}

function fmtPhone(p) {
  if (!p) return '';
  const d = p.replace(/\D/g, '');
  if (d.length === 10) return `(${d.slice(0,3)}) ${d.slice(3,6)}-${d.slice(6)}`;
  return p;
}

function renderResults(rows) {
  currentResults = rows;
  if (rows.length === 0) {
    metaEl.textContent = 'No matches.';
    // remove existing rows
    [...resultsEl.querySelectorAll('.row')].forEach(r => r.remove());
    return;
  }
  metaEl.textContent = `${rows.length} match${rows.length === 1 ? '' : 'es'}`;
  [...resultsEl.querySelectorAll('.row')].forEach(r => r.remove());
  rows.forEach((row, idx) => {
    const el = document.createElement('div');
    el.className = 'row';
    el.dataset.idx = idx;
    el.innerHTML = `
      <div>
        <div class="name">${escapeHtml(row.tenant_name || row.contact_name || '(unknown)')}</div>
        <div class="addr">${escapeHtml(row.property_address || '—')}</div>
        <div class="meta-line">${escapeHtml(row.case_number || '')} · court ${row.court_date || '—'}</div>
      </div>
      <div class="right">
        <div class="phone">${fmtPhone(row.phone) || '—'}</div>
        <div class="loc">${escapeHtml((row.state || '') + '/' + (row.county || ''))}</div>
      </div>
    `;
    el.addEventListener('click', () => selectRow(idx));
    resultsEl.appendChild(el);
  });
  activeRowIndex = -1;
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  })[c]);
}

function selectRow(idx) {
  if (idx < 0 || idx >= currentResults.length) return;
  activeRowIndex = idx;
  [...resultsEl.querySelectorAll('.row')].forEach((r, i) =>
    r.classList.toggle('active', i === idx)
  );
  renderDetail(currentResults[idx]);
}

function relativeTime(iso) {
  if (!iso) return 'never';
  const then = new Date(iso).getTime();
  const diff = Date.now() - then;
  if (diff < 60_000) return 'just now';
  if (diff < 3600_000) return `${Math.round(diff/60000)}m ago`;
  if (diff < 86_400_000) return `${Math.round(diff/3600000)}h ago`;
  return `${Math.round(diff/86400000)}d ago`;
}

async function renderDetail(row) {
  selectedCase = { case_number: row.case_number, track: row.track || 'ng' };
  detailEl.classList.remove('empty');
  const stage = row.bland_status || row.lead_bucket || '—';
  const lastCalled = relativeTime(row.last_called_at);
  detailEl.innerHTML = `
    <h2>${escapeHtml(row.tenant_name || row.contact_name || '(unknown)')}</h2>
    <div class="sub">${escapeHtml(row.state || '')}/${escapeHtml(row.county || '')} · ${escapeHtml(row.case_number || '')}</div>
    <div class="sub">Filed ${row.filing_date || '—'} · Court ${row.court_date || '—'}</div>
    <div class="phone-large" id="phone-large" title="click to copy">${fmtPhone(row.phone) || '—'}</div>
    <div class="addr-large">${escapeHtml(row.property_address || '')}</div>
    <hr>
    <div class="kv"><span class="k">Stage</span><span>${escapeHtml(String(stage))}</span></div>
    <div class="kv"><span class="k">Last called</span><span id="last-called">${lastCalled}</span></div>
    <div class="actions">
      <button id="btn-call">Mark Called</button>
      <button id="btn-note">Add Note</button>
      <button id="btn-skip" class="skip">Skip</button>
    </div>
    <div class="note-form" id="note-form">
      <textarea id="note-text" placeholder="Note (Cmd/Ctrl+Enter to save)"></textarea>
      <div style="margin-top:6px"><button id="btn-save-note">Save</button></div>
    </div>
    <div class="notes" id="notes"><div class="meta">Notes</div></div>
  `;

  document.getElementById('phone-large').addEventListener('click', () => {
    const phone = row.phone || '';
    if (phone) {
      navigator.clipboard.writeText(phone).then(() => showToast('Copied'));
    }
  });
  document.getElementById('btn-call').addEventListener('click', markCalled);
  document.getElementById('btn-note').addEventListener('click', () => {
    document.getElementById('note-form').classList.toggle('open');
    document.getElementById('note-text').focus();
  });
  document.getElementById('btn-save-note').addEventListener('click', saveNote);
  document.getElementById('btn-skip').addEventListener('click', skipLead);
  document.getElementById('note-text').addEventListener('keydown', e => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') saveNote();
  });

  loadNotes();
}

async function markCalled() {
  if (!selectedCase) return;
  const r = await fetch(`/api/leads/${encodeURIComponent(selectedCase.case_number)}/mark-called?track=${selectedCase.track}`, { method: 'POST' });
  if (r.ok) {
    const body = await r.json();
    document.getElementById('last-called').textContent = relativeTime(body.last_called_at);
    showToast('Marked called');
  } else {
    showToast('Failed to mark called');
  }
}

async function saveNote() {
  if (!selectedCase) return;
  const ta = document.getElementById('note-text');
  const text = (ta.value || '').trim();
  if (!text) return;
  const r = await fetch(`/api/leads/${encodeURIComponent(selectedCase.case_number)}/note?track=${selectedCase.track}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  });
  if (r.ok) {
    ta.value = '';
    document.getElementById('note-form').classList.remove('open');
    showToast('Note saved');
    loadNotes();
  } else {
    showToast('Save failed');
  }
}

async function skipLead() {
  if (!selectedCase) return;
  if (!confirm('Skip this lead? This is irreversible.')) return;
  const r = await fetch(`/api/leads/${encodeURIComponent(selectedCase.case_number)}/skip?track=${selectedCase.track}`, { method: 'POST' });
  if (r.ok) {
    showToast('Skipped');
    detailEl.className = 'detail empty';
    detailEl.innerHTML = '<span>Click a result to see details</span>';
  } else {
    showToast('Skip failed');
  }
}

async function loadNotes() {
  if (!selectedCase) return;
  const r = await fetch(`/api/leads/${encodeURIComponent(selectedCase.case_number)}/notes?track=${selectedCase.track}`);
  if (!r.ok) return;
  const notes = await r.json();
  const wrap = document.getElementById('notes');
  if (!wrap) return;
  wrap.innerHTML = `<div class="meta">Notes (${notes.length})</div>` +
    notes.map(n => `
      <div class="note">${escapeHtml(n.note_text)}
        <div class="when">${relativeTime(n.created_at)} · ${escapeHtml(n.author)}</div>
      </div>
    `).join('');
}

let typingTimer;
input.addEventListener('input', () => {
  clearTimeout(typingTimer);
  typingTimer = setTimeout(runSearch, 250);
});
input.addEventListener('keydown', e => {
  if (e.key === 'ArrowDown') { e.preventDefault(); selectRow(Math.min(activeRowIndex + 1, currentResults.length - 1)); }
  else if (e.key === 'ArrowUp') { e.preventDefault(); selectRow(Math.max(activeRowIndex - 1, 0)); }
  else if (e.key === 'Enter' && activeRowIndex < 0 && currentResults.length > 0) {
    selectRow(0);
  } else if (e.key === 'Escape') {
    detailEl.className = 'detail empty';
    detailEl.innerHTML = '<span>Click a result to see details</span>';
    selectedCase = null;
  }
});
document.addEventListener('keydown', e => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
    e.preventDefault();
    input.focus();
    input.select();
  }
});

async function runSearch() {
  const q = input.value.trim();
  if (q.length < 2) {
    metaEl.textContent = 'Type 2+ characters to search.';
    [...resultsEl.querySelectorAll('.row')].forEach(r => r.remove());
    return;
  }
  if (abortController) abortController.abort();
  abortController = new AbortController();
  try {
    const r = await fetch(`/api/search?q=${encodeURIComponent(q)}`, { signal: abortController.signal });
    if (!r.ok) { metaEl.textContent = 'Search error.'; return; }
    const rows = await r.json();
    renderResults(rows);
  } catch (e) {
    if (e.name !== 'AbortError') metaEl.textContent = 'Search error.';
  }
}
</script>
</body>
</html>
```

- [ ] **Step 2: Run the routing tests again to confirm nothing regressed**

```bash
python -m pytest tests/test_dashboard_search_routes.py -v
```

Expected: still 7 passed.

- [ ] **Step 3: Commit**

```bash
git add dashboard/search.html
git commit -m "feat: search.html — search-first dashboard frontend"
```

---

## Task 7: Smoke-run the whole dashboard locally

**Files:** none (manual verification).

The migration hasn't been applied yet, so notes + mark-called endpoints will fail at runtime against Supabase. Search itself should work because it uses existing columns.

- [ ] **Step 1: Boot the dashboard**

```bash
cd "d:\Freelance Projects\EvictionCommand\leadgen"
sh scripts/start_dashboard.sh
```

Or if that script isn't convenient, run uvicorn directly:

```bash
python -m uvicorn dashboard.main:app --host 127.0.0.1 --port 8000
```

- [ ] **Step 2: Open the search page in a browser**

http://127.0.0.1:8000/

Expected: dark-theme page with a centered search input, "Type 2+ characters to search." below it. Empty right-side panel.

- [ ] **Step 3: Type a known tenant name**

Use a name you know exists in production (e.g. one of the Davidson or Harris filings).

Expected: results stream in after ~250ms with the standard row layout (name, address, case#, court date, phone, state/county).

- [ ] **Step 4: Confirm the queue is still accessible**

http://127.0.0.1:8000/queue

Expected: the original brand/view dashboard renders unchanged.

- [ ] **Step 5: Confirm endpoint behavior (mark-called + notes will fail without migration)**

Try clicking "Mark Called" on a result. Without migration 014 applied, this returns a Supabase error. That's expected — Task 8 applies the migration.

- [ ] **Step 6: Stop the server**

Ctrl+C in the terminal running uvicorn. No commit needed for this task.

---

## Task 8: Apply migration 014 to Supabase

**Files:** none in repo (operational).

- [ ] **Step 1: Open the Supabase SQL Editor**

https://supabase.com/dashboard/project/ctdypakgsqupuqtmgxqz/sql/new

- [ ] **Step 2: Paste the contents of `migrations/014_lead_notes_and_last_called.sql`**

The full SQL is in the file from Task 1. Paste it into the SQL Editor and click Run.

Expected output: "Success. No rows returned."

- [ ] **Step 3: Verify both additions are live**

In a new terminal:

```bash
cd "d:\Freelance Projects\EvictionCommand\leadgen"
python -c "
from dotenv import load_dotenv; load_dotenv()
from services.dedup_service import _client
lc = _client.table('lead_contacts').select('*').limit(1).execute()
ln = _client.table('lead_notes').select('*').limit(1).execute()
lc_cols = set(lc.data[0].keys()) if lc.data else set()
print('lead_contacts.last_called_at:', 'last_called_at' in lc_cols)
print('lead_notes table accessible:', ln.data is not None)
"
```

Expected output:
```
lead_contacts.last_called_at: True
lead_notes table accessible: True
```

- [ ] **Step 4: Re-run the local dashboard and confirm Mark Called + Notes work end-to-end**

```bash
python -m uvicorn dashboard.main:app --host 127.0.0.1 --port 8000
```

In the browser: search for a lead, click into the detail panel, click "Mark Called". You should see the "Marked called" toast and the "Last called" line update to "just now". Then click "Add Note", type some text, save. Toast appears, note shows up in the Notes section below.

- [ ] **Step 5: Ctrl+C to stop the server. No commit needed.**

---

## Task 9: Full test suite + push

- [ ] **Step 1: Run the full pytest suite**

```bash
cd "d:\Freelance Projects\EvictionCommand\leadgen"
python -m pytest --tb=short -q
```

Expected: baseline pass rate (542 from end of Spec 2) plus the new tests from this plan (~12 new). One pre-existing DeKalb failure stays.

- [ ] **Step 2: Push to origin/main**

```bash
git push origin main
```

- [ ] **Step 3: Trigger a Railway redeploy so production picks up the new routes**

```bash
railway redeploy --service leadgen --yes
```

Wait ~2-3 minutes for the deploy to complete.

- [ ] **Step 4: Verify production dashboard**

Open the production dashboard URL (the Railway-deployed app's domain). The root URL should now serve the search-first page. The `/queue` URL should serve the legacy multi-tab UI.

---

## Final review checklist

- [ ] Migration 014 applied to Supabase (lead_notes + last_called_at column)
- [ ] Four new service helpers (search_leads, add_lead_note, list_lead_notes, mark_lead_called) exist + tested
- [ ] Five new dashboard routes (/, /queue, /api/search, /api/leads/{c}/note, /api/leads/{c}/notes, /api/leads/{c}/mark-called) wired
- [ ] dashboard/search.html renders end-to-end with typeahead, click-to-select, mark called, add note, skip, click-to-copy phone
- [ ] dashboard/index.html (legacy queue) still accessible at /queue with no regressions
- [ ] Full pytest suite still passes (one pre-existing DeKalb failure unchanged)
- [ ] Pushed to origin/main, Railway redeployed, production dashboard serves the new UI
