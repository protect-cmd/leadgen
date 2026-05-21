# Pre-Test Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three confirmed bugs before the next live SearchBug enrichment run so we don't waste credits on bad data or noisy logs.

**Architecture:** Two code fixes (parse_name suffix stripping, Melissa noise suppression), one commit of already-edited proof script. Each fix is independently testable and commits cleanly on its own.

**Tech Stack:** Python 3.11, pytest, SQLite (enrichment cache), requests (SearchBug)

---

### Task 1: Fix `parse_name` generational suffix stripping

**Files:**
- Modify: `services/name_utils.py:36-41`
- Test: `tests/test_name_utils.py`

Generational suffixes (Jr, Sr, II, III, IV) appear as the last token in a name like
"KENT ANTHONY MCNEAL II". The current code takes `tokens[-1]` unconditionally,
returning `last="II"` instead of `last="MCNEAL"`. The fix strips trailing suffix
tokens before selecting the last name.

- [ ] **Step 1: Write failing tests**

Add to `tests/test_name_utils.py` (append after existing tests):

```python
# ── generational suffix stripping ─────────────────────────────────────────

def test_parse_name_suffix_ii():
    assert parse_name("KENT ANTHONY MCNEAL II") == ("KENT", "MCNEAL")

def test_parse_name_suffix_jr():
    assert parse_name("ROBERT SMITH JR") == ("ROBERT", "SMITH")

def test_parse_name_suffix_sr():
    assert parse_name("JAMES BROWN SR.") == ("JAMES", "BROWN")

def test_parse_name_suffix_iii():
    assert parse_name("WILLIAM DUPREE III") == ("WILLIAM", "DUPREE")

def test_parse_name_suffix_iv():
    assert parse_name("CHARLES LILLY IV") == ("CHARLES", "LILLY")

def test_parse_name_no_suffix_unchanged():
    # "MCNEAL" should not be misidentified as a suffix
    assert parse_name("KENT MCNEAL") == ("KENT", "MCNEAL")

def test_parse_name_comma_suffix_ignored():
    # Comma format — suffix handling only needed for space-separated
    assert parse_name("MCNEAL, KENT II") == ("KENT", "MCNEAL")
```

- [ ] **Step 2: Run to verify they fail**

```
pytest tests/test_name_utils.py -k "suffix" -v
```

Expected: FAILED (parse_name returns ("KENT", "II") not ("KENT", "MCNEAL"))

- [ ] **Step 3: Implement the fix**

In `services/name_utils.py`, add the suffix constant and update `parse_name`:

```python
from __future__ import annotations

_GENERATIONAL_SUFFIXES: frozenset[str] = frozenset({"jr", "sr", "ii", "iii", "iv"})


def _is_middle_initial(token: str) -> bool:
    """Single letter or single letter followed by a period."""
    t = token.rstrip(".")
    return len(t) == 1


def parse_name(raw: str) -> tuple[str, str]:
    """Parse a raw court name into (first_name, last_name).

    Handles:
    - "LAST, FIRST"
    - "LAST, FIRST MIDDLE"  -> middle stripped
    - "FIRST LAST"
    - "FIRST MIDDLE LAST"   -> middle stripped
    - "FIRST [MIDDLE] LAST JR/SR/II/III/IV"  -> suffix stripped
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

    # Space-separated: "FIRST [MIDDLE] LAST [SUFFIX]" or "FIRST LAST"
    tokens = raw.split()
    if len(tokens) < 2:
        return "", ""

    first = tokens[0]
    remaining = tokens[1:]

    # Strip trailing generational suffixes (Jr, Sr, II, III, IV)
    while remaining and remaining[-1].rstrip(".").lower() in _GENERATIONAL_SUFFIXES:
        remaining.pop()

    last = remaining[-1] if remaining else tokens[-1]
    return first, last
```

- [ ] **Step 4: Run all name_utils tests**

```
pytest tests/test_name_utils.py -v
```

Expected: all pass (26 existing + 7 new = 33 total)

- [ ] **Step 5: Commit**

```
git add services/name_utils.py tests/test_name_utils.py
git commit -m "fix: strip generational suffixes (Jr/Sr/II/III/IV) in parse_name"
```

---

### Task 2: Suppress Melissa noise in `enrich_tenant_by_name`

**Files:**
- Modify: `services/batchdata_service.py:329` and `services/batchdata_service.py:365`
- Test: `tests/test_batchdata_yellow_enrichment.py`

`enrich_tenant_by_name` calls `enrich_tenant()` at two points (cache-hit path and
live-hit path) without passing `use_melissa_fallback=False`. Melissa isn't licensed,
so every call logs a GE29 error that obscures real problems. The fix adds the keyword
argument at both call sites.

- [ ] **Step 1: Write failing test**

Add to `tests/test_batchdata_yellow_enrichment.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.mark.asyncio
async def test_enrich_tenant_by_name_no_melissa_fallback(sample_yellow_filing):
    """enrich_tenant must be called with use_melissa_fallback=False at both call sites."""
    fake_contact = EnrichedContact(
        filing=sample_yellow_filing, track="ng", phone="5131112222",
        dnc_status="unknown", dnc_source="searchbug",
    )
    with (
        patch("services.batchdata_service.EnrichmentCache") as mock_cache_cls,
        patch("services.batchdata_service._searchbug_search", new_callable=AsyncMock) as mock_sb,
        patch("services.batchdata_service.enrich_tenant", new_callable=AsyncMock) as mock_et,
    ):
        mock_cache = MagicMock()
        mock_cache.get.return_value = None
        mock_cache.check_daily_cap.return_value = True
        mock_cache_cls.return_value = mock_cache
        mock_sb.return_value = ("5131112222", "456 Oak St, Cincinnati, OH 45202")
        mock_et.return_value = fake_contact

        from services.batchdata_service import enrich_tenant_by_name
        await enrich_tenant_by_name(sample_yellow_filing)

        # enrich_tenant must have been called with use_melissa_fallback=False
        assert mock_et.call_count >= 1
        for call in mock_et.call_args_list:
            assert call.kwargs.get("use_melissa_fallback") is False, (
                f"enrich_tenant called without use_melissa_fallback=False: {call}"
            )
```

- [ ] **Step 2: Run to verify it fails**

```
pytest tests/test_batchdata_yellow_enrichment.py::test_enrich_tenant_by_name_no_melissa_fallback -v
```

Expected: FAILED (enrich_tenant called without use_melissa_fallback=False)

- [ ] **Step 3: Apply the fix**

In `services/batchdata_service.py`, update line 329 (cache-hit path):

```python
# Before:
result = await enrich_tenant(patched, lookup_property_if_missing=lookup_property_if_missing)

# After (line 329):
result = await enrich_tenant(
    patched,
    lookup_property_if_missing=lookup_property_if_missing,
    use_melissa_fallback=False,
)
```

And line 365 (live-hit path) — same change:

```python
# Before:
result = await enrich_tenant(patched, lookup_property_if_missing=lookup_property_if_missing)

# After (line 365):
result = await enrich_tenant(
    patched,
    lookup_property_if_missing=lookup_property_if_missing,
    use_melissa_fallback=False,
)
```

- [ ] **Step 4: Run all yellow enrichment tests**

```
pytest tests/test_batchdata_yellow_enrichment.py -v
```

Expected: all pass

- [ ] **Step 5: Commit**

```
git add services/batchdata_service.py tests/test_batchdata_yellow_enrichment.py
git commit -m "fix: suppress Melissa fallback in enrich_tenant_by_name (not licensed)"
```

---

### Task 3: Commit proof script cleanup

**Files:**
- Commit: `scripts/proof_hamilton_yellow_enrichment.py` (already edited)

The proof script has already had Unicode characters (`→`, `─`) replaced with ASCII
equivalents (`->`, `-`) and unused imports/variables removed. These edits are in
the file but were never committed. This task just stages and commits them.

- [ ] **Step 1: Verify the edits are present**

```
git diff scripts/proof_hamilton_yellow_enrichment.py
```

Confirm the diff shows `→` replaced with `->` and `─` replaced with `-`.

- [ ] **Step 2: Confirm tests still pass**

```
pytest tests/ -v --tb=short -q
```

Expected: all pass

- [ ] **Step 3: Commit**

```
git add scripts/proof_hamilton_yellow_enrichment.py
git commit -m "fix: replace Unicode arrows/separators with ASCII in proof script (cp1252 compat)"
```

---

### Task 4: Research Hamilton case detail page for defendant address

**Files:**
- No code changes — research only

Hamilton's eviction schedule at `courtclerk.org` has a case detail page at
`https://www.courtclerk.org/case_summary.php?casenumber=<CASE_NUMBER>`.
If this page exposes the defendant's actual property address, Hamilton could be
upgraded from yellow to green (skip SearchBug, use BatchData address lookup
which has ~50-60% hit rate vs ~15% for yellow sources).

- [ ] **Step 1: Fetch a known case detail page**

Use a case number from a recent Hamilton scrape (e.g., `26CV14017`). Fetch:
`https://www.courtclerk.org/case_summary.php?casenumber=26CV14017`

Look for any field labeled: defendant address, property address, premises, or similar.

- [ ] **Step 2: Document findings**

If defendant address IS present:
- Note the HTML element/selector that contains it
- Note whether it's a full street address (number + street) or just city/zip
- Add a comment to `scrapers/ohio/hamilton.py` near `property_address="Cincinnati, OH"`:
  ```python
  # TODO(green-upgrade): case detail page at courtclerk.org/case_summary.php?casenumber=X
  # exposes defendant address at <selector>. Fetch and use it to upgrade Hamilton to green.
  ```

If defendant address is NOT present:
- Add a comment to `scrapers/ohio/hamilton.py`:
  ```python
  # Note: case detail page does not expose defendant address. Hamilton stays yellow.
  ```

- [ ] **Step 3: Commit the comment**

```
git add scrapers/ohio/hamilton.py
git commit -m "docs: note Hamilton case detail page address availability (green-upgrade research)"
```
