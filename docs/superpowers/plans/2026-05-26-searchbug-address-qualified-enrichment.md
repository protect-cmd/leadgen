# SearchBug Address-Qualified Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce paid tenant-enrichment ambiguity by querying SearchBug with the known court street address and preventing cache reuse across distinct address-qualified searches.

**Architecture:** Green-source tenant queries will add SearchBug's supported `ADDRESS` parameter after stripping unit/apartment identifiers, while city/state/ZIP remain part of the request. The local SearchBug cache will key on the complete query locator, including ZIP and supplied street address, so name-only and address-qualified queries remain isolated. Yellow-source queries keep their no-street behavior but gain ZIP-aware cache separation because they already send ZIP.

**Tech Stack:** Python, SQLite cache, `httpx`, `pytest`

---

### Task 1: Address-Qualified SearchBug Request

**Files:**
- Modify: `services/searchbug_service.py`
- Modify: `services/batchdata_service.py`
- Modify: `scripts/enrich_supabase_green_a_searchbug.py`
- Test: `tests/test_searchbug_service.py`
- Test: `tests/test_batchdata_green_enrichment.py`

- [ ] Add failing tests showing green-source queries submit a street `ADDRESS` without unit identifiers.
- [ ] Run focused tests and confirm failure is due to the missing address parameter.
- [ ] Add a shared query-street normalizer and pass `ADDRESS` through the SearchBug wrapper.
- [ ] Route green-source and CSV enrichment calls through that address-qualified request.

### Task 2: Query-Identity Cache Isolation

**Files:**
- Modify: `services/enrichment_cache.py`
- Modify: `services/batchdata_service.py`
- Test: `tests/test_enrichment_cache.py`
- Test: `tests/test_batchdata_green_enrichment.py`
- Test: `tests/test_batchdata_yellow_enrichment.py`

- [ ] Add a failing test proving two addresses for the same name and city cannot share a cache result.
- [ ] Replace the cache key schema with name, city/state, ZIP, and query address, preserving legacy cache rows as unqualified queries.
- [ ] Pass ZIP and query address consistently at green and yellow cache call sites.
- [ ] Run focused enrichment tests followed by `pytest -q`.

### Task 3: Paid Batch Preflight

**Files:**
- No production data writes.

- [ ] Recalculate the 10-day approved, clean-person, current-court-date candidate count.
- [ ] Confirm no paid SearchBug call has been executed during implementation or validation.
- [ ] Present the address-qualified first-batch recommendation for approval before spending credits.
