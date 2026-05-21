# Tenant-First Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorient the pipeline around tenant contactability by adding independent track feature flags and fixing the yellow lead second-call escalation bug.

**Architecture:** Replace the implicit `GHL_NG_LOCATION_ID`-based NG toggle with two independent boolean flags (`TENANT_TRACK_ENABLED`, `LANDLORD_TRACK_ENABLED`); fix `enrich_tenant_by_name` so phone+address SearchBug hits no longer auto-escalate to a second paid BatchData call; gate the address-only rescue path behind `YELLOW_SECOND_CALL_ENABLED`.

**Tech Stack:** Python asyncio, pytest, existing batchdata_service / runner.py patterns

---

## File Map

| File | Change |
|---|---|
| `services/batchdata_service.py` | Fix `enrich_tenant_by_name` second-call logic at **two places**: live-call path (lines 367–383) and cache-hit path (lines 326–342) |
| `pipeline/runner.py` | Replace `_NG_ENABLED` with runtime reads of `TENANT_TRACK_ENABLED` / `LANDLORD_TRACK_ENABLED`; gate EC enrich; fail-fast on invalid config; handle `ec_contact=None` downstream |
| `tests/test_batchdata_yellow_enrichment.py` | Remove 3 tests that assumed old second-call behavior; add 6 new tests covering all result-type combinations |
| `tests/test_runner_tracks.py` | New file: 5 tests for independent track flag behavior in runner |

---

## Task 1: Fix yellow second-call logic — live SearchBug call path

**Files:**
- Modify: `services/batchdata_service.py:367-383`
- Modify: `tests/test_batchdata_yellow_enrichment.py`

The live-call path in `enrich_tenant_by_name` currently calls `enrich_tenant()` (a second paid BatchData call) whenever `resolved_address` is returned by SearchBug — even when `phone` was also found. Per spec:
- phone + address → store both, no second call
- address only → check `YELLOW_SECOND_CALL_ENABLED`
- phone only → unchanged (return phone, no second call)

- [ ] **Step 1: Write failing tests for the new behaviors**

Add these three tests to `tests/test_batchdata_yellow_enrichment.py` (append before the last line):

```python
@pytest.mark.asyncio
async def test_searchbug_phone_and_address_stores_both_no_second_call(mock_cache):
    """SearchBug returns phone+address → store both; do NOT auto-run second paid call."""
    filing = _filing(tenant_name="BRETT LILLY")
    resolved = "456 Oak St, Cincinnati, OH 45202"

    with patch("services.enrichment_cache.get_cache", return_value=mock_cache), \
         patch("services.searchbug_service.search_tenant", new_callable=AsyncMock,
               return_value=("5131112222", resolved)), \
         patch("services.batchdata_service.enrich_tenant", new_callable=AsyncMock) as mock_enrich:
        result = await batchdata_service.enrich_tenant_by_name(filing)

    mock_enrich.assert_not_called()
    assert result.phone == "5131112222"
    assert result.secondary_address == resolved
    assert result.dnc_source == "searchbug"


@pytest.mark.asyncio
async def test_searchbug_address_only_no_second_call_by_default(mock_cache):
    """SearchBug returns address but no phone → no second paid call when YELLOW_SECOND_CALL_ENABLED=false (default)."""
    filing = _filing(tenant_name="BRETT LILLY")
    resolved = "123 Elm St, Cincinnati, OH 45202"

    with patch("services.enrichment_cache.get_cache", return_value=mock_cache), \
         patch("services.searchbug_service.search_tenant", new_callable=AsyncMock,
               return_value=(None, resolved)), \
         patch("services.batchdata_service.enrich_tenant", new_callable=AsyncMock) as mock_enrich:
        result = await batchdata_service.enrich_tenant_by_name(filing)

    mock_enrich.assert_not_called()
    assert result.phone is None
    assert result.secondary_address == resolved
    assert result.dnc_source == "searchbug"


@pytest.mark.asyncio
async def test_searchbug_address_only_triggers_second_call_when_enabled(mock_cache, monkeypatch):
    """SearchBug returns address but no phone → enrich_tenant called when YELLOW_SECOND_CALL_ENABLED=true."""
    monkeypatch.setenv("YELLOW_SECOND_CALL_ENABLED", "true")
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
```

- [ ] **Step 2: Run them to confirm they fail**

```
cd "d:\Freelance Projects\EvictionCommand\leadgen"
pytest tests/test_batchdata_yellow_enrichment.py::test_searchbug_phone_and_address_stores_both_no_second_call tests/test_batchdata_yellow_enrichment.py::test_searchbug_address_only_no_second_call_by_default tests/test_batchdata_yellow_enrichment.py::test_searchbug_address_only_triggers_second_call_when_enabled -v
```

Expected: FAIL (current code always calls `enrich_tenant` when `resolved_address` is truthy).

- [ ] **Step 3: Replace lines 367–383 in batchdata_service.py**

Current code (lines 367–383):

```python
        if resolved_address:
            patched = filing.model_copy(update={"property_address": resolved_address})
            result = await enrich_tenant(
                patched,
                lookup_property_if_missing=lookup_property_if_missing,
                use_melissa_fallback=False,
            )
            if not result.phone and phone:
                result = _dc_replace(result, phone=phone, dnc_source="searchbug")
            return result

        if phone:
            log.info(f"enrich_tenant_by_name: SearchBug phone-only hit for {filing.case_number}")
            return EnrichedContact(
                filing=filing, track="ng", phone=phone,
                dnc_status="unknown", dnc_source="searchbug",
            )
```

Replace with:

```python
        if phone and resolved_address:
            # phone + address: store both; do NOT auto-run a second paid call
            log.info(
                f"enrich_tenant_by_name: SearchBug phone+address hit for {filing.case_number}"
            )
            return EnrichedContact(
                filing=filing, track="ng", phone=phone,
                secondary_address=resolved_address,
                dnc_status="unknown", dnc_source="searchbug",
            )

        if resolved_address:
            # address only: rescue path — second paid call only if explicitly enabled
            _second_call_enabled = (
                os.environ.get("YELLOW_SECOND_CALL_ENABLED", "false").lower() == "true"
            )
            if _second_call_enabled:
                log.info(
                    f"enrich_tenant_by_name: address-only hit, running second call "
                    f"for {filing.case_number}"
                )
                patched = filing.model_copy(update={"property_address": resolved_address})
                return await enrich_tenant(
                    patched,
                    lookup_property_if_missing=lookup_property_if_missing,
                    use_melissa_fallback=False,
                )
            log.info(
                f"enrich_tenant_by_name: address-only hit, second call disabled "
                f"for {filing.case_number}"
            )
            return EnrichedContact(
                filing=filing, track="ng", phone=None, email=None,
                secondary_address=resolved_address,
                dnc_status="unknown", dnc_source="searchbug",
            )

        if phone:
            log.info(f"enrich_tenant_by_name: SearchBug phone-only hit for {filing.case_number}")
            return EnrichedContact(
                filing=filing, track="ng", phone=phone,
                dnc_status="unknown", dnc_source="searchbug",
            )
```

- [ ] **Step 4: Run the three new tests — expect PASS**

```
pytest tests/test_batchdata_yellow_enrichment.py::test_searchbug_phone_and_address_stores_both_no_second_call tests/test_batchdata_yellow_enrichment.py::test_searchbug_address_only_no_second_call_by_default tests/test_batchdata_yellow_enrichment.py::test_searchbug_address_only_triggers_second_call_when_enabled -v
```

Expected: all 3 PASS.

- [ ] **Step 5: Remove two old tests that tested the replaced behavior**

Delete `test_searchbug_address_triggers_batchdata` (lines 90–110) and `test_enrich_tenant_by_name_no_melissa_fallback` (lines 201–223) from `tests/test_batchdata_yellow_enrichment.py`. Both verified the old "always call enrich_tenant when address returned" logic which is now wrong.

- [ ] **Step 6: Run full yellow enrichment suite — expect all pass**

```
pytest tests/test_batchdata_yellow_enrichment.py -v
```

Expected: all remaining tests PASS.

- [ ] **Step 7: Commit**

```bash
git add services/batchdata_service.py tests/test_batchdata_yellow_enrichment.py
git commit -m "feat: fix yellow second-call live-call path — phone+address no longer auto-escalates to BatchData"
```

---

## Task 2: Fix yellow second-call logic — cache hit path

**Files:**
- Modify: `services/batchdata_service.py:326-342`
- Modify: `tests/test_batchdata_yellow_enrichment.py`

The cache-hit path has the identical bug. When `(phone, resolved_address)` is retrieved from cache, the code always calls `enrich_tenant()` if `resolved_address` is truthy — even when `phone` was also cached. The same result table applies.

- [ ] **Step 1: Write failing tests for cache hit combinations**

Add these three tests to `tests/test_batchdata_yellow_enrichment.py`:

```python
@pytest.mark.asyncio
async def test_cache_hit_phone_and_address_no_second_call(mock_cache):
    """Cache hit with phone+address → return both; do NOT call enrich_tenant."""
    filing = _filing(tenant_name="BRETT LILLY")
    resolved = "456 Oak Ave, Cincinnati, OH 45202"
    mock_cache.set("brett", "lilly", "cincinnati", "oh", "5550001111", resolved)

    with patch("services.enrichment_cache.get_cache", return_value=mock_cache), \
         patch("services.searchbug_service.search_tenant", new_callable=AsyncMock) as mock_sb, \
         patch("services.batchdata_service.enrich_tenant", new_callable=AsyncMock) as mock_enrich:
        result = await batchdata_service.enrich_tenant_by_name(filing)

    mock_sb.assert_not_called()
    mock_enrich.assert_not_called()
    assert result.phone == "5550001111"
    assert result.secondary_address == resolved
    assert result.dnc_source == "searchbug"


@pytest.mark.asyncio
async def test_cache_hit_address_only_no_second_call_by_default(mock_cache):
    """Cache hit with address but no phone → no second paid call when YELLOW_SECOND_CALL_ENABLED=false."""
    filing = _filing(tenant_name="BRETT LILLY")
    resolved = "456 Oak Ave, Cincinnati, OH 45202"
    mock_cache.set("brett", "lilly", "cincinnati", "oh", None, resolved)

    with patch("services.enrichment_cache.get_cache", return_value=mock_cache), \
         patch("services.batchdata_service.enrich_tenant", new_callable=AsyncMock) as mock_enrich:
        result = await batchdata_service.enrich_tenant_by_name(filing)

    mock_enrich.assert_not_called()
    assert result.phone is None
    assert result.secondary_address == resolved


@pytest.mark.asyncio
async def test_cache_hit_address_only_triggers_second_call_when_enabled(mock_cache, monkeypatch):
    """Cache hit with address-only → calls enrich_tenant when YELLOW_SECOND_CALL_ENABLED=true."""
    monkeypatch.setenv("YELLOW_SECOND_CALL_ENABLED", "true")
    filing = _filing(tenant_name="BRETT LILLY")
    resolved = "456 Oak Ave, Cincinnati, OH 45202"
    mock_cache.set("brett", "lilly", "cincinnati", "oh", None, resolved)
    mock_enriched = EnrichedContact(
        filing=filing, track="ng", phone="5559998888", dnc_status="clear", dnc_source="batchdata"
    )

    with patch("services.enrichment_cache.get_cache", return_value=mock_cache), \
         patch("services.batchdata_service.enrich_tenant", new_callable=AsyncMock,
               return_value=mock_enriched) as mock_enrich:
        result = await batchdata_service.enrich_tenant_by_name(filing)

    mock_enrich.assert_called_once()
    patched_filing = mock_enrich.call_args[0][0]
    assert patched_filing.property_address == resolved
    assert result.phone == "5559998888"
```

- [ ] **Step 2: Run them to confirm they fail**

```
pytest tests/test_batchdata_yellow_enrichment.py::test_cache_hit_phone_and_address_no_second_call tests/test_batchdata_yellow_enrichment.py::test_cache_hit_address_only_no_second_call_by_default tests/test_batchdata_yellow_enrichment.py::test_cache_hit_address_only_triggers_second_call_when_enabled -v
```

Expected: FAIL (cache path unconditionally calls `enrich_tenant` when `resolved_address` truthy).

- [ ] **Step 3: Replace lines 326–342 in batchdata_service.py**

Current code (lines 326–342):

```python
        if cached is not None:
            phone, resolved_address = cached
            if resolved_address:
                patched = filing.model_copy(update={"property_address": resolved_address})
                result = await enrich_tenant(
                    patched,
                    lookup_property_if_missing=lookup_property_if_missing,
                    use_melissa_fallback=False,
                )
                if not result.phone and phone:
                    result = _dc_replace(result, phone=phone, dnc_source="searchbug")
                return result
            if phone:
                return EnrichedContact(
                    filing=filing, track="ng", phone=phone,
                    dnc_status="unknown", dnc_source="searchbug",
                )
            continue  # cached miss — try next name
```

Replace with:

```python
        if cached is not None:
            phone, resolved_address = cached
            if phone and resolved_address:
                # phone + address cached: store both; do NOT auto-run a second paid call
                return EnrichedContact(
                    filing=filing, track="ng", phone=phone,
                    secondary_address=resolved_address,
                    dnc_status="unknown", dnc_source="searchbug",
                )
            if resolved_address:
                # address only cached: rescue path — second paid call only if explicitly enabled
                _second_call_enabled = (
                    os.environ.get("YELLOW_SECOND_CALL_ENABLED", "false").lower() == "true"
                )
                if _second_call_enabled:
                    patched = filing.model_copy(update={"property_address": resolved_address})
                    return await enrich_tenant(
                        patched,
                        lookup_property_if_missing=lookup_property_if_missing,
                        use_melissa_fallback=False,
                    )
                return EnrichedContact(
                    filing=filing, track="ng", phone=None, email=None,
                    secondary_address=resolved_address,
                    dnc_status="unknown", dnc_source="searchbug",
                )
            if phone:
                return EnrichedContact(
                    filing=filing, track="ng", phone=phone,
                    dnc_status="unknown", dnc_source="searchbug",
                )
            continue  # cached miss — try next name
```

- [ ] **Step 4: Run the three new cache tests — expect PASS**

```
pytest tests/test_batchdata_yellow_enrichment.py::test_cache_hit_phone_and_address_no_second_call tests/test_batchdata_yellow_enrichment.py::test_cache_hit_address_only_no_second_call_by_default tests/test_batchdata_yellow_enrichment.py::test_cache_hit_address_only_triggers_second_call_when_enabled -v
```

Expected: all 3 PASS.

- [ ] **Step 5: Update the old cache test that assumed old behavior**

In `tests/test_batchdata_yellow_enrichment.py`, replace `test_cache_hit_with_address_triggers_batchdata` (lines 177–198) — it tests the old "cache address → always calls enrich_tenant" logic. Replace it with a test that matches the new address-only + enabled behavior:

```python
@pytest.mark.asyncio
async def test_cache_hit_address_only_and_second_call_enabled_calls_batchdata(mock_cache, monkeypatch):
    """Cache hit with address-only → enrich_tenant called only when YELLOW_SECOND_CALL_ENABLED=true."""
    monkeypatch.setenv("YELLOW_SECOND_CALL_ENABLED", "true")
    filing = _filing(tenant_name="BRETT LILLY")
    resolved = "456 Oak Ave, Cincinnati, OH 45202"
    mock_cache.set("brett", "lilly", "cincinnati", "oh", None, resolved)

    mock_enriched = EnrichedContact(
        filing=filing, track="ng", phone="5559998888", dnc_status="clear", dnc_source="batchdata"
    )

    with patch("services.enrichment_cache.get_cache", return_value=mock_cache), \
         patch("services.searchbug_service.search_tenant", new_callable=AsyncMock) as mock_sb, \
         patch("services.batchdata_service.enrich_tenant", new_callable=AsyncMock,
               return_value=mock_enriched) as mock_enrich:
        result = await batchdata_service.enrich_tenant_by_name(filing)

    mock_sb.assert_not_called()
    mock_enrich.assert_called_once()
    patched_filing = mock_enrich.call_args[0][0]
    assert patched_filing.property_address == resolved
    assert result.phone == "5559998888"
```

- [ ] **Step 6: Run the full yellow enrichment suite**

```
pytest tests/test_batchdata_yellow_enrichment.py -v
```

Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add services/batchdata_service.py tests/test_batchdata_yellow_enrichment.py
git commit -m "feat: fix yellow second-call cache path — phone+address hit no longer auto-escalates"
```

---

## Task 3: Add independent track flags and refactor runner.py enrichment orchestration

**Files:**
- Modify: `pipeline/runner.py`
- Create: `tests/test_runner_tracks.py`

Replace the implicit `_NG_ENABLED = bool(os.getenv("GHL_NG_LOCATION_ID", ""))` toggle with two runtime-read flags. Reading inside `run()` (not as module-level constants) lets tests `monkeypatch.setenv` without re-importing. EC track must be gated by `LANDLORD_TRACK_ENABLED`. Defaults: tenant=true, landlord=false.

- [ ] **Step 1: Write failing tests**

Create `tests/test_runner_tracks.py`:

```python
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.contact import EnrichedContact
from models.filing import Filing


def _filing(**kwargs) -> Filing:
    values = dict(
        case_number="TEST-001",
        tenant_name="Jane Doe",
        property_address="123 Main St, Nashville, TN 37211",
        landlord_name="Bob Smith",
        filing_date=date(2026, 5, 16),
        state="TN",
        county="Davidson",
        notice_type="Eviction",
        source_url="https://example.test",
        property_type_hint="residential",
    )
    values.update(kwargs)
    return Filing(**values)


def _ec_contact(filing):
    return EnrichedContact(
        filing=filing, track="ec", phone="6151111111",
        dnc_status="clear", dnc_source="batchdata",
        property_type="residential", estimated_rent=1800,
    )


def _ng_contact(filing):
    return EnrichedContact(
        filing=filing, track="ng", phone="6152222222",
        dnc_status="clear", dnc_source="batchdata",
        property_type="residential", estimated_rent=1800,
    )


def _base_patches():
    """Common service patches that let a filing reach the enrichment block."""
    return [
        patch("services.dedup_service.is_duplicate", new_callable=AsyncMock, return_value=False),
        patch("services.dedup_service.insert_filing", new_callable=AsyncMock),
        patch("services.dedup_service.update_language_hint", new_callable=AsyncMock),
        patch("services.dedup_service.update_enrichment", new_callable=AsyncMock),
        patch("services.dedup_service.update_classification", new_callable=AsyncMock),
        patch("services.dedup_service.update_ghl_id", new_callable=AsyncMock),
        patch("services.dedup_service.set_bland_status", new_callable=AsyncMock),
        patch("services.dedup_service.write_run_metrics", new_callable=AsyncMock),
        patch("services.geocode_service.normalize_address", new_callable=AsyncMock, return_value=None),
        patch("services.language_service.language_hint_for_name", return_value=None),
        patch("services.notification_service.send_run_summary", new_callable=AsyncMock),
        patch("pipeline.runner._process_track", new_callable=AsyncMock,
              return_value=MagicMock(ghl_created=True, instantly_enrolled=False, instantly_error=None)),
    ]


@pytest.fixture(autouse=True)
def ghl_stage_ids(monkeypatch):
    monkeypatch.setenv("GHL_NEW_FILING_STAGE_ID", "stage-ec")
    monkeypatch.setenv("GHL_NG_NEW_FILING_STAGE_ID", "stage-ng")


@pytest.mark.asyncio
async def test_tenant_only_mode_calls_enrich_tenant_not_enrich(monkeypatch):
    """TENANT_TRACK_ENABLED=true, LANDLORD_TRACK_ENABLED=false → enrich() never called."""
    monkeypatch.setenv("TENANT_TRACK_ENABLED", "true")
    monkeypatch.setenv("LANDLORD_TRACK_ENABLED", "false")
    filing = _filing()

    patches = _base_patches() + [
        patch("services.batchdata_service.enrich", new_callable=AsyncMock) as mock_enrich,
        patch("services.batchdata_service.enrich_tenant", new_callable=AsyncMock,
              return_value=_ng_contact(filing)) as mock_enrich_tenant,
    ]

    import contextlib
    with contextlib.ExitStack() as stack:
        mocks = [stack.enter_context(p) for p in patches]
        mock_enrich = mocks[-2]
        mock_enrich_tenant = mocks[-1]
        from pipeline import runner
        await runner.run([filing], state="TN", county="Davidson")

    mock_enrich.assert_not_called()
    mock_enrich_tenant.assert_called_once()


@pytest.mark.asyncio
async def test_landlord_only_mode_calls_enrich_not_enrich_tenant(monkeypatch):
    """TENANT_TRACK_ENABLED=false, LANDLORD_TRACK_ENABLED=true → enrich_tenant() never called."""
    monkeypatch.setenv("TENANT_TRACK_ENABLED", "false")
    monkeypatch.setenv("LANDLORD_TRACK_ENABLED", "true")
    filing = _filing()

    import contextlib
    patches = _base_patches() + [
        patch("services.batchdata_service.enrich", new_callable=AsyncMock,
              return_value=_ec_contact(filing)),
        patch("services.batchdata_service.enrich_tenant", new_callable=AsyncMock),
    ]
    with contextlib.ExitStack() as stack:
        mocks = [stack.enter_context(p) for p in patches]
        mock_enrich = mocks[-2]
        mock_enrich_tenant = mocks[-1]
        from pipeline import runner
        await runner.run([filing], state="TN", county="Davidson")

    mock_enrich.assert_called_once()
    mock_enrich_tenant.assert_not_called()


@pytest.mark.asyncio
async def test_dual_track_mode_calls_both(monkeypatch):
    """TENANT_TRACK_ENABLED=true, LANDLORD_TRACK_ENABLED=true → both enrich() and enrich_tenant() called."""
    monkeypatch.setenv("TENANT_TRACK_ENABLED", "true")
    monkeypatch.setenv("LANDLORD_TRACK_ENABLED", "true")
    filing = _filing()

    import contextlib
    patches = _base_patches() + [
        patch("services.batchdata_service.enrich", new_callable=AsyncMock,
              return_value=_ec_contact(filing)),
        patch("services.batchdata_service.enrich_tenant", new_callable=AsyncMock,
              return_value=_ng_contact(filing)),
    ]
    with contextlib.ExitStack() as stack:
        mocks = [stack.enter_context(p) for p in patches]
        mock_enrich = mocks[-2]
        mock_enrich_tenant = mocks[-1]
        from pipeline import runner
        await runner.run([filing], state="TN", county="Davidson")

    mock_enrich.assert_called_once()
    mock_enrich_tenant.assert_called_once()


@pytest.mark.asyncio
async def test_both_tracks_disabled_raises_runtime_error(monkeypatch):
    """TENANT_TRACK_ENABLED=false, LANDLORD_TRACK_ENABLED=false → RuntimeError raised."""
    monkeypatch.setenv("TENANT_TRACK_ENABLED", "false")
    monkeypatch.setenv("LANDLORD_TRACK_ENABLED", "false")

    from pipeline import runner
    with pytest.raises(RuntimeError, match="TENANT_TRACK_ENABLED|both.*disabled|Invalid config"):
        await runner.run([_filing()], state="TN", county="Davidson")


@pytest.mark.asyncio
async def test_default_config_is_tenant_only(monkeypatch):
    """No track env vars set → defaults to tenant-only (TENANT=true, LANDLORD=false)."""
    monkeypatch.delenv("TENANT_TRACK_ENABLED", raising=False)
    monkeypatch.delenv("LANDLORD_TRACK_ENABLED", raising=False)
    filing = _filing()

    import contextlib
    patches = _base_patches() + [
        patch("services.batchdata_service.enrich", new_callable=AsyncMock),
        patch("services.batchdata_service.enrich_tenant", new_callable=AsyncMock,
              return_value=_ng_contact(filing)),
    ]
    with contextlib.ExitStack() as stack:
        mocks = [stack.enter_context(p) for p in patches]
        mock_enrich = mocks[-2]
        mock_enrich_tenant = mocks[-1]
        from pipeline import runner
        await runner.run([filing], state="TN", county="Davidson")

    mock_enrich.assert_not_called()
    mock_enrich_tenant.assert_called_once()
```

- [ ] **Step 2: Run tests to confirm they fail**

```
pytest tests/test_runner_tracks.py -v
```

Expected: FAIL — `_NG_ENABLED` still controls NG; EC has no flag; no RuntimeError raised.

- [ ] **Step 3: Remove the module-level _NG_ENABLED constant from runner.py**

Remove line 33 from `pipeline/runner.py`:

```python
_NG_ENABLED = bool(os.getenv("GHL_NG_LOCATION_ID", ""))
```

- [ ] **Step 4: Add fail-fast validation at the start of run()**

In `pipeline/runner.py`, after line 254 (`log.info(f"Runner received {len(filings)} filings")`), insert:

```python
    tenant_track_enabled = os.getenv("TENANT_TRACK_ENABLED", "true").lower() == "true"
    landlord_track_enabled = os.getenv("LANDLORD_TRACK_ENABLED", "false").lower() == "true"
    if not tenant_track_enabled and not landlord_track_enabled:
        raise RuntimeError(
            "Invalid configuration: TENANT_TRACK_ENABLED and LANDLORD_TRACK_ENABLED "
            "cannot both be false. Set at least one to 'true'."
        )
```

- [ ] **Step 5: Replace the enrichment block inside the filing loop**

In `pipeline/runner.py`, replace lines 304–336 (the `enrich_ng` block and `try` clause):

```python
        enrich_ng = _NG_ENABLED and not _is_business_name(filing.tenant_name)
        if _NG_ENABLED and not enrich_ng:
            log.info(f"{filing.case_number} NG skipped: tenant looks like business")

        try:
            property_info = None
            property_lookup_calls = 0
            if filing.property_type_hint is None:
                property_info = await batchdata_service.lookup_property_info(filing)
                property_lookup_calls = 1

            if enrich_ng:
                ec_contact, ng_contact = await asyncio.gather(
                    batchdata_service.enrich(
                        filing,
                        property_info=property_info,
                        lookup_property_if_missing=False,
                    ),
                    batchdata_service.enrich_tenant(
                        filing,
                        property_info=property_info,
                        lookup_property_if_missing=False,
                    ),
                )
                m["batchdata_calls"] += property_lookup_calls + 2
            else:
                ec_contact = await batchdata_service.enrich(
                    filing,
                    property_info=property_info,
                    lookup_property_if_missing=False,
                )
                ng_contact = None
                m["batchdata_calls"] += property_lookup_calls + 1
```

With:

```python
        enrich_tenant_flag = tenant_track_enabled and not _is_business_name(filing.tenant_name)
        if tenant_track_enabled and not enrich_tenant_flag:
            log.info(f"{filing.case_number} tenant track skipped: tenant looks like business")

        try:
            property_info = None
            property_lookup_calls = 0
            if filing.property_type_hint is None:
                property_info = await batchdata_service.lookup_property_info(filing)
                property_lookup_calls = 1

            if landlord_track_enabled and enrich_tenant_flag:
                ec_contact, ng_contact = await asyncio.gather(
                    batchdata_service.enrich(
                        filing,
                        property_info=property_info,
                        lookup_property_if_missing=False,
                    ),
                    batchdata_service.enrich_tenant(
                        filing,
                        property_info=property_info,
                        lookup_property_if_missing=False,
                    ),
                )
                m["batchdata_calls"] += property_lookup_calls + 2
            elif enrich_tenant_flag:
                ng_contact = await batchdata_service.enrich_tenant(
                    filing,
                    property_info=property_info,
                    lookup_property_if_missing=False,
                )
                ec_contact = None
                m["batchdata_calls"] += property_lookup_calls + 1
            elif landlord_track_enabled:
                ec_contact = await batchdata_service.enrich(
                    filing,
                    property_info=property_info,
                    lookup_property_if_missing=False,
                )
                ng_contact = None
                m["batchdata_calls"] += property_lookup_calls + 1
            else:
                log.info(
                    f"{filing.case_number} skipped: business name tenant and landlord track disabled"
                )
                continue
```

- [ ] **Step 6: Fix downstream code that assumes ec_contact is never None**

In `pipeline/runner.py`, replace lines 346–368:

```python
        ec_contact.language_hint = language_hint
        if ng_contact is not None:
            ng_contact.language_hint = language_hint

        if ec_contact.phone:
            m["phones_found"] += 1
        if ng_contact is not None and ng_contact.phone:
            m["phones_found"] += 1

        await dedup_service.update_enrichment(ec_contact)
        if ng_contact is not None:
            await dedup_service.update_enrichment(ng_contact)

        lead_bucket = await _classify_and_store(filing, ec_contact)
```

With:

```python
        if ec_contact is not None:
            ec_contact.language_hint = language_hint
            if ec_contact.phone:
                m["phones_found"] += 1
            await dedup_service.update_enrichment(ec_contact)
        if ng_contact is not None:
            ng_contact.language_hint = language_hint
            if ng_contact.phone:
                m["phones_found"] += 1
            await dedup_service.update_enrichment(ng_contact)

        classify_contact = ec_contact or ng_contact
        lead_bucket = await _classify_and_store(filing, classify_contact)
```

And replace the `_process_track` task setup (lines 365–368):

```python
        tasks = [_process_track(ec_contact)]
        if ng_contact is not None:
            tasks.append(_process_track(ng_contact))
```

With:

```python
        tasks = []
        if ec_contact is not None:
            tasks.append(_process_track(ec_contact))
        if ng_contact is not None:
            tasks.append(_process_track(ng_contact))
```

- [ ] **Step 7: Run the track tests — expect PASS**

```
pytest tests/test_runner_tracks.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 8: Run the full test suite to catch regressions**

```
pytest -v
```

Expected: all tests PASS. Fix any failures before committing.

- [ ] **Step 9: Commit**

```bash
git add pipeline/runner.py tests/test_runner_tracks.py
git commit -m "feat: add TENANT/LANDLORD track flags; fail-fast on invalid config; handle ec_contact=None"
```

---

## Deployment note

The new defaults are:
- `TENANT_TRACK_ENABLED=true` (was: controlled by `GHL_NG_LOCATION_ID` presence)
- `LANDLORD_TRACK_ENABLED=false` (was: always on)

**Before deploying to Railway:** Set `LANDLORD_TRACK_ENABLED=true` on any environment that was running EC (landlord/Grant Ellis) enrichment, or the EC track will be silently disabled by the new default.
