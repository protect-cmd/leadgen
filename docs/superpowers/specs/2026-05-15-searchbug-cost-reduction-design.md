---
title: SearchBug Cost Reduction Plan
date: 2026-05-15
status: approved
---

# SearchBug Cost Reduction Plan

## Context

Yellow-status court sources (Hamilton County OH, Georgia Magistrate courts, TN General Sessions, Maricopa AZ, etc.) yield tenant name + city/state only — no street address. SearchBug People Search (`api_ppl`) resolves a current address and phone from name + city/state. A live Hamilton County OH test of 20 names returned 3 phone hits (~15%) at ~$3/usable number on the base PPD plan ($0.77/hit). The goal is to lower cost-per-usable-phone as far as possible before scaling to Georgia and other yellow counties.

**Root causes of high cost:**
- SearchBug charges for every response with records, including ambiguous multi-match results we reject (rows > 1)
- Middle initial parsing errors (e.g., `BRETT L LILLY` → LNAME=`L LILLY`) produce zero-match no-charges but also zero hits
- Multi-tenant strings (e.g., `AVONTE THOMAS ASHANTE JOHNSON`) are sent as a single name and return nothing
- No caching: repeated names for the same city re-incur costs
- No daily cap: runaway batch can drain balance

---

## Section 1 — Name Parsing

### `services/name_utils.py`

#### `parse_name(raw: str) -> tuple[str, str]`

Parses a single-person name string into `(first_name, last_name)`.

Input formats handled:
- `LAST, FIRST` → `("FIRST", "LAST")`
- `LAST, FIRST MIDDLE` → `("FIRST", "LAST")` (middle stripped)
- `LAST, FIRST M.` → `("FIRST", "LAST")` (middle initial stripped)
- `FIRST LAST` → `("FIRST", "LAST")`
- `FIRST MIDDLE LAST` → `("FIRST", "LAST")` (middle stripped)
- `FIRST M. LAST` → `("FIRST", "LAST")` (middle initial stripped)

Middle initial detection: any token that is 1–2 characters or ends with `.` is considered a middle initial and dropped.

Returns `("", "")` if fewer than two meaningful tokens remain.

#### `split_tenants(raw: str) -> list[str]`

Detects multi-person strings and returns a list of individual name strings.

**Algorithm**: Tokenize by spaces. Walk tokens left-to-right maintaining a window. When the window contains exactly 2 tokens (potential first+last pair) and the next token is capitalized and not a suffix, treat the window as one person and start a new window. Minimum viable person = 2 tokens.

Examples:
- `"AVONTE THOMAS ASHANTE JOHNSON"` → `["AVONTE THOMAS", "ASHANTE JOHNSON"]`
- `"BRETT L LILLY"` → `["BRETT L LILLY"]` (single person, handled by parse_name)
- `"JOHN SMITH"` → `["JOHN SMITH"]`
- `"JOHN SMITH JANE DOE"` → `["JOHN SMITH", "JANE DOE"]`

Returns a list of raw name strings; each is then fed individually through `parse_name`.

---

## Section 2 — Pre-call Filters + ZIP Map

### `is_common_surname(last_name: str) -> bool`

Returns `True` if `last_name` (case-insensitive) is in the top-300 US Census surnames. When a surname is common (Smith, Johnson, Williams, Brown, Jones, Davis, Miller, Wilson, Moore, Taylor, Anderson, Thomas, Jackson…), there is a high probability that SearchBug returns multiple matches for name + city → `rows > 1` → we pay and get nothing.

**Filter behavior:** Skip the SearchBug call entirely and return `(None, None)` with a log entry. This is a no-charge outcome vs. the current $0.77 charge for an ambiguous result.

The surname list is compiled from the US Census 2010 Frequently Occurring Surnames file. Top 300 surnames cover the majority of common-name false-charge cases.

### `resolve_zip(city: str, state: str) -> str`

Returns a representative ZIP code for a known city+state pair, used to narrow SearchBug results when the yellow source only provides `"City, STATE"`.

**Why ZIP helps:** SearchBug's `ZIP` parameter restricts results to that postal area, significantly reducing multi-match probability for common names in large metros.

Hardcoded map (17 entries, covers all in-scope yellow counties as of 2026-05-15):

| City | State | ZIP | Source county |
|------|-------|-----|---------------|
| Cincinnati | OH | 45202 | Hamilton |
| Cleveland | OH | 44113 | Cuyahoga |
| Dayton | OH | 45402 | Montgomery |
| Columbus | OH | 43215 | Franklin |
| Atlanta | GA | 30303 | Fulton / re:SearchGA |
| Griffin | GA | 30223 | Spalding |
| Marietta | GA | 30060 | Cobb |
| Decatur | GA | 30030 | DeKalb |
| Chattanooga | TN | 37402 | Hamilton TN |
| Gallatin | TN | 37066 | Sumner |
| Knoxville | TN | 37902 | Knox |
| Nashville | TN | 37201 | Davidson |
| Phoenix | AZ | 85001 | Maricopa |
| Scottsdale | AZ | 85251 | Maricopa East |
| Las Vegas | NV | 89101 | Clark |
| Reno | NV | 89501 | Washoe |
| Austin | TX | 78701 | Travis |

Returns `""` (empty string) for unknown cities; SearchBug call still proceeds without ZIP in that case.

---

## Section 3 — Cache + Daily Cap

### `services/enrichment_cache.py`

#### SQLite cache

File: `data/enrichment_cache.db` (auto-created on first use, not committed to git).

Schema:
```sql
CREATE TABLE searchbug_cache (
    first_name TEXT NOT NULL,
    last_name  TEXT NOT NULL,
    city       TEXT NOT NULL,
    state      TEXT NOT NULL,
    phone      TEXT,          -- NULL = confirmed miss
    address    TEXT,          -- NULL = no address returned
    cached_at  REAL NOT NULL, -- Unix timestamp
    PRIMARY KEY (first_name, last_name, city, state)
);
```

**Key:** `(first_name, last_name, city, state)` — all lowercased before lookup.

**TTL:** 30 days. Entries older than 30 days are treated as cache misses and re-queried. A background purge runs at app startup (or on first cache access).

**Hit/miss both cached:** When SearchBug returns 0 results or rows > 1 (ambiguous), we store `phone=NULL, address=NULL`. Re-running a batch 2 days later won't re-charge for the same name. This is the primary cost-reduction mechanism for repeated batches.

#### Daily cap

Controlled by env var `SEARCHBUG_DAILY_CAP` (default: `100`). Counts live API calls made today (UTC date). When the cap is reached, remaining names in the batch are skipped and logged as `daily_cap_exceeded`.

Cap state is stored in the same SQLite DB:
```sql
CREATE TABLE daily_cap (
    date TEXT PRIMARY KEY, -- YYYY-MM-DD
    count INTEGER NOT NULL DEFAULT 0
);
```

This ensures cap persists across process restarts within the same calendar day.

---

## Section 4 — Full Execution Flow

### Updated `enrich_tenant_by_name` in `services/batchdata_service.py`

```
filing.tenant_name
    │
    ▼
split_tenants(raw)          → list of raw name strings
    │
    ▼ (for each name)
parse_name(raw)             → (first_name, last_name)
    │                         skip if ("", "")
    ▼
cache.get(first, last,      → hit? return cached EnrichedContact
          city, state)        miss? continue
    │
    ▼
is_common_surname(last)     → True? skip call, cache miss, log
    │
    ▼
resolve_zip(city, state)    → postal string (may be "")
    │
    ▼
daily_cap.check()           → exceeded? skip, log
    │
    ▼
searchbug_service.search_tenant(first, last, city, state, postal)
    │                         → (phone, resolved_address)
    ▼
cache.set(first, last,      store result (hit or miss)
          city, state,
          phone, address)
    │
    ▼ (if resolved_address)
filing.model_copy(update={"property_address": resolved_address})
    │
    ▼
batchdata_service.enrich_tenant(patched_filing)
    │                         → BatchData skip-trace with name validation
    ▼
EnrichedContact (track="ng")
```

**Multi-tenant handling:** `split_tenants` returns a list. Each sub-name produces one `EnrichedContact`. The caller (pipeline or job) receives a list and chooses which contacts to push (first match, all matches, or first-with-phone).

**Fallback chain for a single person:**
1. BatchData skip-trace returns phone with name match → use it
2. BatchData returns no match → SearchBug phone returned directly (dnc_source="searchbug")
3. Neither returns phone → unenriched contact (phone=None)

### Updated `searchbug_service.search_tenant`

Signature unchanged. Internal change: `resolve_zip` is no longer called here — the caller (`enrich_tenant_by_name`) resolves and passes `postal`. No other changes to SearchBug service.

---

## Expected Impact

| Lever | Mechanism | Expected reduction |
|-------|-----------|-------------------|
| Name parsing fix | Middle initials stripped → no-charge zero-results become real hits | +5–10% hit rate |
| Multi-tenant split | 2-person strings become 2 calls, each with a real chance of single match | +3–5% hit rate |
| Common surname filter | Skip calls for top-300 surnames → avoid $0.77 ambiguous charges | −20–30% cost |
| ZIP narrowing | Reduces multi-match probability for large metros | −15–25% ambiguous rate |
| 30-day cache | Repeated names across batches cost $0 after first call | −50%+ cost on overlapping batches |
| Daily cap | Prevents runaway balance drain | Risk control |

**Target:** From ~$3/usable number → ~$0.50–$1.00 once cache warms and filters engage.

---

## Files Changed

| File | Change |
|------|--------|
| `services/name_utils.py` | New — parse_name, split_tenants, is_common_surname, resolve_zip |
| `services/enrichment_cache.py` | New — SQLite cache + daily cap |
| `services/batchdata_service.py` | Update enrich_tenant_by_name to use utils + cache |
| `services/searchbug_service.py` | Minor — remove internal ZIP resolution (caller handles it) |
| `.env.example` | Add SEARCHBUG_DAILY_CAP=100 |

## Out of Scope

- LLM-based disambiguation (too expensive per call, diminishing returns vs. rule-based filters)
- Melissa Personator (not licensed on current account)
- Trestle / Pipl (no name-lookup API; Pipl requires sales contact)
- DNC scrubbing changes (existing BatchData DNC flags unchanged)
