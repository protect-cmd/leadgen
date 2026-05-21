# Tenant-First Enrichment — Summary

## What Changed

### 1. Independent Track Feature Flags (`pipeline/runner.py`)

Replaced the implicit `GHL_NG_LOCATION_ID` toggle with two explicit boolean env vars:

| Variable | Default | Controls |
|---|---|---|
| `TENANT_TRACK_ENABLED` | `true` | NG (Vantage Defense / tenant) enrichment |
| `LANDLORD_TRACK_ENABLED` | `false` | EC (Grant Ellis / landlord) enrichment |

- Both false → `RuntimeError` at startup (fail-fast)
- Tenant-only is now the default production posture
- Set `LANDLORD_TRACK_ENABLED=true` in Railway to re-enable landlord track

### 2. Yellow Lead Second-Call Bug Fix (`services/batchdata_service.py`)

SearchBug people-search returns `(phone, address)`. The old code always escalated to a second paid BatchData call whenever an address was returned — even when a phone was already found. Fixed at both the live-call path and cache-hit path:

| SearchBug result | Old behavior | New behavior |
|---|---|---|
| phone + address | paid BatchData call | store both, no second call |
| address only | paid BatchData call | store address; second call only if `YELLOW_SECOND_CALL_ENABLED=true` |
| phone only | unchanged | unchanged |

New env var: `YELLOW_SECOND_CALL_ENABLED` (default `false`).

### 3. Melissa → SearchBug Fallback Swap (`services/batchdata_service.py`)

When BatchData's skip-trace returns a name mismatch (wrong person at address), the old code called Melissa Personator as a fallback. Melissa is not licensed (GE29 error on every call). Replaced with a SearchBug people-search call using the tenant name + city/state/ZIP extracted from the geocoded property address.

---

## Live Validation Run

**Ohio — Hamilton County (Cincinnati), 1-day lookback**

| Metric | Value |
|---|---|
| Filings received | 57 |
| Duplicates skipped | 0 |
| Discarded (pre-enrichment) | 1 |
| BatchData API calls | 112 |
| Phone numbers found | 5 |
| GHL contacts created | 5 |
| EC contacts created | 0 |
| Run time | ~598s |

**Observations:**
- All 57 filings routed as `[NG]` only — `LANDLORD_TRACK_ENABLED=false` confirmed working
- `enrich_tenant()` called exclusively (not `enrich()`) — tenant-first path active
- Successful name matches → GHL contact created + queued for Bland review
- Name mismatches → SearchBug fallback attempted (credentials not yet set in Railway, so `phone=no` on all mismatches — same outcome as before but now through the correct code path)
- Instantly skipped on all leads (NG campaign ID not configured yet)

**Phone hit rate: 8.8%** (5/57) — reflects BatchData name-match rate only; SearchBug fallback yield will improve once credentials are in Railway.

---

## People-Search Provider Comparison (Pending)

Currently using **SearchBug** for:
- Yellow-source tenant lookup (name + city/state → phone)
- Green-source name-mismatch fallback (name + city/state/ZIP → phone)

**Enformion** is a candidate alternative. No account or credits yet — evaluation pending. When available: benchmark Enformion vs SearchBug on name-mismatch hit rate across a sample of Hamilton/Franklin filings.

---

## Deployment Checklist

- [ ] Set `SEARCHBUG_CO_CODE` and `SEARCHBUG_API_KEY` in Railway (enables name-mismatch fallback yield)
- [ ] Confirm `LANDLORD_TRACK_ENABLED=false` in Railway (current default, correct for tenant-only mode)
- [ ] Set `LANDLORD_TRACK_ENABLED=true` if/when EC track needs to run alongside tenant track
- [ ] Leave `YELLOW_SECOND_CALL_ENABLED=false` unless second paid call on address-only hits is explicitly wanted
