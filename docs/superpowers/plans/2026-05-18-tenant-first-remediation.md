# Tenant-First Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the tenant-first rollout by wiring yellow leads into production, preventing unapproved second paid calls on green leads, and promoting recovered yellow addresses into downstream data.

**Architecture:** Keep the existing tenant/landlord track switches, but add an explicit green-vs-yellow routing decision in `pipeline/runner.py`. Keep `enrich_tenant_by_name()` as the yellow people-search path, make green SearchBug fallback opt-in and mismatch-only, and ensure recovered yellow addresses become the active filing address before downstream systems use them.

**Tech Stack:** Python 3, pytest, existing async pipeline services, SearchBug, BatchData, Supabase-backed lead persistence.

---

## File Map

| File | Responsibility |
|---|---|
| `pipeline/runner.py` | Decide green vs yellow path and orchestrate enabled tracks. |
| `services/batchdata_service.py` | Tenant enrichment behavior, yellow recovery handling, green fallback gating. |
| `tests/test_runner_tracks.py` | Production routing coverage for green/yellow and track flags. |
| `tests/test_batchdata_yellow_enrichment.py` | Yellow-path call-count and recovered-address behavior. |
| `tests/test_batchdata_optimization.py` | Green-path paid-call behavior and fallback opt-in behavior. |
| `docs/tenant-first-enrichment-summary.md` | Keep implementation summary truthful after remediation. |

## Task 1: Route Yellow Leads Through Production Runner

**Files:**
- Modify: `pipeline/runner.py`
- Modify: `tests/test_runner_tracks.py`

- [ ] **Step 1: Write the failing yellow-routing test**

Add a test proving that an addressless filing reaches `enrich_tenant_by_name()` instead of being skipped:

```python
@pytest.mark.asyncio
async def test_yellow_filing_uses_people_search_path(monkeypatch):
    monkeypatch.setenv("TENANT_TRACK_ENABLED", "true")
    monkeypatch.setenv("LANDLORD_TRACK_ENABLED", "false")
    filing = _filing(property_address="Cincinnati, OH", state="OH", county="Hamilton")

    with contextlib.ExitStack() as stack:
        patches = _base_patches(filing)
        patches.append(
            patch(
                "services.batchdata_service.enrich_tenant_by_name",
                new_callable=AsyncMock,
                return_value=_ng_contact(filing),
            )
        )
        mocks = [stack.enter_context(p) for p in patches]
        mock_enrich = mocks[-3]
        mock_enrich_tenant = mocks[-2]
        mock_enrich_tenant_by_name = mocks[-1]
        from pipeline import runner
        await runner.run([filing], state="OH", county="Hamilton")

    mock_enrich.assert_not_called()
    mock_enrich_tenant.assert_not_called()
    mock_enrich_tenant_by_name.assert_called_once()
```

- [ ] **Step 2: Run the targeted test and confirm it fails**

Run:

```powershell
pytest -q tests/test_runner_tracks.py::test_yellow_filing_uses_people_search_path
```

Expected: FAIL because the runner currently exits early for unusable addresses and never calls `enrich_tenant_by_name()`.

- [ ] **Step 3: Add a small source-path helper**

In `pipeline/runner.py`, add:

```python
def _is_green_filing(filing: Filing) -> bool:
    return _is_usable_address(filing.property_address)
```

Use this to make the green/yellow distinction explicit.

- [ ] **Step 4: Replace the early unusable-address skip with yellow-path handling**

Update the geocode / address section so:

```python
normalized = await geocode_service.normalize_address(filing.property_address)
if normalized:
    log.debug(f"{filing.case_number} address normalized: {normalized}")
    filing.property_address = normalized

is_green = _is_green_filing(filing)
```

Do **not** `continue` merely because the address is not usable when tenant track is enabled; let yellow leads proceed to classification and people search.

- [ ] **Step 5: Route enrichment by source path**

Replace the current tenant-only branch with:

```python
if landlord_track_enabled and enrich_tenant_flag and is_green:
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
elif enrich_tenant_flag and is_green:
    ng_contact = await batchdata_service.enrich_tenant(
        filing,
        property_info=property_info,
        lookup_property_if_missing=False,
    )
    ec_contact = None
    m["batchdata_calls"] += property_lookup_calls + 1
elif enrich_tenant_flag:
    ng_contact = await batchdata_service.enrich_tenant_by_name(
        filing,
        lookup_property_if_missing=False,
    )
    ec_contact = None
    m["batchdata_calls"] += property_lookup_calls
elif landlord_track_enabled and is_green:
    ec_contact = await batchdata_service.enrich(
        filing,
        property_info=property_info,
        lookup_property_if_missing=False,
    )
    ng_contact = None
    m["batchdata_calls"] += property_lookup_calls + 1
else:
    m["batchdata_calls"] += property_lookup_calls
    log.info(f"{filing.case_number} skipped: no enabled track can use this filing")
    continue
```

- [ ] **Step 6: Avoid useless property lookup for yellow filings**

Guard the property lookup:

```python
if is_green and filing.property_type_hint is None:
    property_info = await batchdata_service.lookup_property_info(filing)
    property_lookup_calls = 1
```

- [ ] **Step 7: Run focused tests**

Run:

```powershell
pytest -q tests/test_runner_tracks.py tests/test_batchdata_optimization.py
```

Expected: PASS.

- [ ] **Step 8: Commit**

```powershell
git add pipeline/runner.py tests/test_runner_tracks.py
git commit -m "fix: route yellow filings through tenant people search"
```

## Task 2: Make Green SearchBug Fallback Explicit and Mismatch-Only

**Files:**
- Modify: `services/batchdata_service.py`
- Modify: `tests/test_batchdata_optimization.py`

- [ ] **Step 1: Write failing tests for fallback policy**

Add:

```python
@pytest.mark.asyncio
async def test_green_tenant_no_phone_does_not_call_searchbug_by_default(monkeypatch):
    monkeypatch.delenv("GREEN_SEARCHBUG_FALLBACK_ENABLED", raising=False)
    filing = _filing()
    response = _batchdata_response_for_matching_tenant_without_phone("Jane Doe")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=response), \
         patch("services.searchbug_service.search_tenant", new_callable=AsyncMock) as mock_sb:
        result = await batchdata_service.enrich_tenant(filing, lookup_property_if_missing=False)

    mock_sb.assert_not_called()
    assert result.phone is None


@pytest.mark.asyncio
async def test_green_tenant_name_mismatch_calls_searchbug_only_when_enabled(monkeypatch):
    monkeypatch.setenv("GREEN_SEARCHBUG_FALLBACK_ENABLED", "true")
    filing = _filing()
    response = _batchdata_response_for_returned_name("Wrong Person")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=response), \
         patch(
             "services.searchbug_service.search_tenant",
             new_callable=AsyncMock,
             return_value=("6155551212", "123 Main St, Nashville, TN 37211"),
         ) as mock_sb:
        result = await batchdata_service.enrich_tenant(filing, lookup_property_if_missing=False)

    mock_sb.assert_called_once()
    assert result.phone == "6155551212"
    assert result.dnc_source == "searchbug"
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run:

```powershell
pytest -q tests/test_batchdata_optimization.py -k "green_tenant_no_phone or green_tenant_name_mismatch"
```

Expected: FAIL because fallback currently runs on any missing phone and is not gated by env config.

- [ ] **Step 3: Add the new config gate**

In `services/batchdata_service.py`, change:

```python
if not phone:
```

to:

```python
green_searchbug_fallback_enabled = (
    os.environ.get("GREEN_SEARCHBUG_FALLBACK_ENABLED", "false").lower() == "true"
)
if green_searchbug_fallback_enabled and not name_matched:
```

- [ ] **Step 4: Update the function docstring**

Replace:

```python
Falls back to SearchBug people-search when BatchData returns no name match.
```

with:

```python
Optionally falls back to SearchBug people-search on confirmed BatchData name mismatch
when GREEN_SEARCHBUG_FALLBACK_ENABLED=true.
```

- [ ] **Step 5: Run focused tests**

Run:

```powershell
pytest -q tests/test_batchdata_optimization.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add services/batchdata_service.py tests/test_batchdata_optimization.py
git commit -m "fix: gate green searchbug fallback behind explicit flag"
```

## Task 3: Promote Recovered Yellow Addresses Into Downstream Filing Data

**Files:**
- Modify: `services/batchdata_service.py`
- Modify: `tests/test_batchdata_yellow_enrichment.py`

- [ ] **Step 1: Write failing tests for phone+address promotion**

Add:

```python
@pytest.mark.asyncio
async def test_searchbug_phone_and_address_promotes_recovered_address(mock_cache):
    filing = _filing(tenant_name="BRETT LILLY")
    resolved = "456 Oak St, Cincinnati, OH 45202"

    with patch("services.enrichment_cache.get_cache", return_value=mock_cache), \
         patch(
             "services.searchbug_service.search_tenant",
             new_callable=AsyncMock,
             return_value=("5131112222", resolved),
         ):
        result = await batchdata_service.enrich_tenant_by_name(filing)

    assert result.filing.property_address == resolved
    assert result.secondary_address == resolved
```

Repeat the same expectation for the cached phone+address path.

- [ ] **Step 2: Run the tests and confirm they fail**

Run:

```powershell
pytest -q tests/test_batchdata_yellow_enrichment.py -k "promotes_recovered_address"
```

Expected: FAIL because current results keep the original city-only filing address.

- [ ] **Step 3: Patch the live phone+address path**

Change:

```python
return EnrichedContact(
    filing=filing, track="ng", phone=phone,
    secondary_address=resolved_address,
    dnc_status="unknown", dnc_source="searchbug",
)
```

to:

```python
patched = filing.model_copy(update={"property_address": resolved_address})
return EnrichedContact(
    filing=patched, track="ng", phone=phone,
    secondary_address=resolved_address,
    dnc_status="unknown", dnc_source="searchbug",
)
```

- [ ] **Step 4: Patch the cached phone+address path the same way**

When both `phone` and `resolved_address` are cached, create the patched filing before returning the contact.

- [ ] **Step 5: Run yellow-path tests**

Run:

```powershell
pytest -q tests/test_batchdata_yellow_enrichment.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add services/batchdata_service.py tests/test_batchdata_yellow_enrichment.py
git commit -m "fix: promote recovered yellow addresses downstream"
```

## Task 4: Refresh Summary Docs and Verify End-to-End

**Files:**
- Modify: `docs/tenant-first-enrichment-summary.md`

- [ ] **Step 1: Update the summary doc**

Revise the summary so it accurately states:

- yellow filings now route through `enrich_tenant_by_name()` in production,
- green SearchBug fallback is opt-in via `GREEN_SEARCHBUG_FALLBACK_ENABLED`,
- recovered yellow addresses are promoted into the contact filing before downstream systems consume them.

- [ ] **Step 2: Run focused verification**

Run:

```powershell
pytest -q tests/test_runner_tracks.py tests/test_batchdata_yellow_enrichment.py tests/test_batchdata_optimization.py
```

Expected: PASS.

- [ ] **Step 3: Run the full suite**

Run:

```powershell
pytest -q
```

Expected: all tenant-first-related tests pass. If the pre-existing DeKalb expectation mismatch still fails, note it separately as an unrelated known failure rather than claiming the suite is green.

- [ ] **Step 4: Commit**

```powershell
git add docs/tenant-first-enrichment-summary.md
git commit -m "docs: refresh tenant-first enrichment summary"
```

## Self-Review

### Spec coverage

- Yellow production routing: Task 1
- No default second paid green call: Task 2
- Recovered yellow address propagation: Task 3
- Documentation / verification: Task 4

### Placeholder scan

No placeholders, TODOs, or “similar to above” instructions remain.

### Type consistency

- Existing service names are preserved: `enrich_tenant()`, `enrich_tenant_by_name()`
- New env var is consistently named `GREEN_SEARCHBUG_FALLBACK_ENABLED`
- Recovered addresses continue to use `secondary_address` while also updating `filing.property_address`

