# Yellow Source Enrichment

## What is a yellow source?

A **yellow source** is a court calendar or filing list that exposes tenant/defendant name and case context (case number, filing date, landlord) but **no property or defendant street address**. The address field on the `Filing` model is set to a city/state placeholder like `"Cincinnati, OH"` or `"Atlanta, GA"`.

Green sources (Harris County TX, Franklin County OH, Hamilton County OH, Clark County NV, etc.) expose a real street address and go straight to BatchData skip-trace. Yellow sources need a separate lookup to resolve a phone number or address from the name alone.

---

## Active yellow sources

| Court | State | Scraper | Volume (est.) | Notes |
|---|---|---|---|---|
| Cobb County Magistrate | GA | `scrapers/georgia/researchga.py` | ~150/wk | PDF calendars via ResearchGA portal |
| DeKalb County Magistrate | GA | `scrapers/georgia/researchga.py` | ~200/wk | Same portal, same scraper |
| Davidson County General Sessions | TN | `scrapers/tennessee/davidson.py` | ~300/wk | Nashville; name + case only |
| Hamilton County General Sessions | TN | *(planned)* | ~150/wk | Chattanooga; no address exposed |
| Sumner County General Sessions | TN | *(planned)* | ~80/wk | Gallatin; no address exposed |
| Maricopa County Justice Courts | AZ | `scrapers/arizona/maricopa.py` | ~500/wk | Phoenix metro; name + case only |

> **Note:** Hamilton County OH Municipal Court was yellow until 2026-05-16, when we discovered the `/data/case_summary.php?sec=party` endpoint exposes defendant addresses. It is now green and goes through BatchData directly.

---

## Enrichment pipeline

Entry point: `batchdata_service.enrich_tenant_by_name(filing)` in [services/batchdata_service.py](../services/batchdata_service.py).

```
Filing (name + city/state only)
  │
  ▼
split_tenants()         ← splits "AVONTE DUPREE ASHANTE LILLY" → ["AVONTE DUPREE", "ASHANTE LILLY"]
  │                        only splits exact 4-token strings with no middle initials
  ▼
parse_name()            ← "BRETT L LILLY" → first="BRETT", last="LILLY"
  │                        strips middle initials and generational suffixes (Jr/Sr/II/III/IV)
  ▼
SQLite cache lookup     ← data/enrichment_cache.db, 30-day TTL, keyed on (first, last, city, state)
  │  None    → not cached, continue
  │  (None,None) → cached miss, skip to next name
  │  (phone, addr) → cached hit, use immediately
  ▼
Common surname filter   ← skip if last name is in top-300 US Census surnames
  │                        ~194 names: Smith, Johnson, Williams, Brown, Jones, ...
  │                        Caches a miss (no charge). No SearchBug call made.
  ▼
Daily cap check         ← SEARCHBUG_DAILY_CAP env var (default: 100 calls/day)
  │                        Hard stop — breaks the loop if cap reached
  ▼
ZIP narrowing           ← resolve_zip(city, state) maps known yellow cities to 5-digit ZIPs
  │                        17 cities mapped: Cincinnati, Atlanta, Nashville, Phoenix, etc.
  │                        Tighter ZIP = fewer ambiguous multi-match results
  ▼
SearchBug People Search ← api_ppl endpoint, $0.77/hit (charged on any response with records)
  │                        FNAME + LNAME + CITY + STATE + ZIP(optional)
  │  rows == 0 → no match, cache miss, try next name
  │  rows > 1  → ambiguous (multi-match), reject, try next name — NO CHARGE
  │  rows == 1 → validate name, extract phone + most-recent address
  ▼
Name validation         ← returned name must fuzzy-match the queried name
  │                        rejects company names, LLC, Corp, etc.
  ▼
  ├─ resolved_address → patch filing.property_address, call enrich_tenant() (BatchData skip-trace)
  │                       use_melissa_fallback=False (Melissa not in use)
  │
  └─ phone only        → return EnrichedContact(phone=..., dnc_status="unknown", dnc_source="searchbug")
                          (no DNC check available without a property address)
```

---

## Services and files

| File | Purpose |
|---|---|
| `services/searchbug_service.py` | SearchBug `api_ppl` HTTP wrapper. Single entry point: `search_tenant(first, last, city, state, postal)` → `(phone, address)` or `(None, None)`. Enforces single-match rule and name validation. |
| `services/enrichment_cache.py` | SQLite-backed cache at `data/enrichment_cache.db`. 30-day TTL. Also tracks daily call count for cap enforcement. `get_cache()` returns a singleton. |
| `services/name_utils.py` | `parse_name`, `split_tenants`, `is_common_surname`, `resolve_zip`. All name pre-processing lives here. |
| `services/batchdata_service.py` | `enrich_tenant_by_name()` orchestrates the full chain. `enrich_tenant()` is the green-path BatchData skip-trace used after an address is resolved. |

---

## What we are NOT using

**Melissa Personator** — not licensed. Explicitly disabled at both SearchBug resolution call sites:
```python
result = await enrich_tenant(patched, use_melissa_fallback=False)
```

**Bright Data** — used only for web scraping and bot-bypass (Tarrant County TX scraper). Not a data product for contact enrichment.

---

## Cost model

SearchBug PPD (Pay Per Data) plan.

| Scenario | Charge? |
|---|---|
| No results (`rows == 0`) | No |
| Multi-match (`rows > 1`) — rejected by our rule | **Yes** — charged even though we discard |
| Single match, name validation fails | **Yes** |
| Single match, name validates, phone/address returned | **Yes** |
| Common surname skipped (pre-call filter) | No |
| Cache hit | No |
| Daily cap reached | No |

**Proof run results (20 Hamilton OH filings, pre-green-upgrade):**

| Metric | Before filters | After filters |
|---|---|---|
| Filings tested | 20 | 20 |
| Common surnames skipped | 0 | 9 (WHITE, JONES, THOMAS, JOHNSON, JORDAN, EDWARDS, KELLY, WOOD, CLARK) |
| Multi-match rejections paid | 5 | 0 |
| Phones found | 3 (15%) | 3 (15%) |
| Est. cost per usable phone | ~$3.33 | ~$1.93 |

The 15% hit rate is a SearchBug data coverage ceiling, not a parsing issue. Common surname filtering eliminated 45% of the calls that would have cost money with no usable result.

---

## Name parsing rules

Handled by `parse_name()` in `services/name_utils.py`.

| Input format | Result |
|---|---|
| `"JOHNSON, MARY"` | `("MARY", "JOHNSON")` |
| `"JOHNSON, MARY ANN"` | `("MARY", "JOHNSON")` — middle stripped |
| `"BRETT L LILLY"` | `("BRETT", "LILLY")` — middle initial stripped |
| `"KENT ANTHONY MCNEAL II"` | `("KENT", "MCNEAL")` — suffix stripped |
| `"ROBERT SMITH JR."` | `("ROBERT", "SMITH")` — suffix stripped |
| `"AVONTE DUPREE"` | `("AVONTE", "DUPREE")` |

Generational suffixes stripped: `jr`, `sr`, `ii`, `iii`, `iv` (case-insensitive, trailing dot tolerated).

Multi-tenant splitting (`split_tenants`): only splits exactly 4-token strings where no token is a middle initial. `"AVONTE DUPREE ASHANTE LILLY"` → `["AVONTE DUPREE", "ASHANTE LILLY"]`. `"BRETT L LILLY"` → `["BRETT L LILLY"]` (3 tokens, not split).

---

## Cache behavior

SQLite at `data/enrichment_cache.db` (gitignored). Keyed on `(first_name, last_name, city, state)` case-insensitively.

| Cached value | Meaning | Action on hit |
|---|---|---|
| `None` | Not in cache | Run the full pipeline |
| `(None, None)` | Confirmed miss (surname skip or SearchBug no-match) | Skip this name — no API call |
| `(phone, None)` | Phone found, no address | Return phone-only contact |
| `(phone, address)` | Full hit | Patch filing with address, run BatchData |
| `(None, address)` | Address found, no phone | Patch filing with address, run BatchData |

TTL: 30 days. Expired entries are pruned on `EnrichmentCache.__init__`.

---

## Configuration

```env
SEARCHBUG_CO_CODE=...       # SearchBug company code
SEARCHBUG_API_KEY=...       # SearchBug API key
SEARCHBUG_DAILY_CAP=100     # Max SearchBug calls per calendar day (default: 100)
```

---

## Known limitations

1. **15% hit rate ceiling** — SearchBug's people search data does not cover ~85% of eviction court defendants. This is a data coverage issue, not a pipeline bug.

2. **Phone-only hits get no DNC check** — when SearchBug returns a phone but no address, we cannot call BatchData (needs a street address). The contact is returned with `dnc_status="unknown"`. These should be manually verified before dialing or passed through a standalone DNC lookup.

3. **4-token split only** — `split_tenants` only handles exactly 4 tokens with no middle initials. `"AVONTE T DUPREE ASHANTE LILLY"` (5 tokens) is passed through unsplit and will fail to parse the second tenant.

4. **AKA aliases in address cells** — some courts embed alias names in the address field (e.g., `AKA CHEYENNE M STROTMAN·108 FERNALD DR`). For yellow sources this doesn't apply (address is city-only), but noted for scrapers that pull from party pages.

5. **Common names produce no lead** — filings where the only tenant has a top-300 surname (e.g., `"JAMES JOHNSON"`) are filtered and never enriched. These represent real eviction filings with no path to a phone under the current pipeline.

---

## Upgrade path: yellow → green

A yellow source becomes green when we can retrieve a defendant street address from a court-adjacent source. Options in priority order:

1. **Court party/case detail page** — fetch per-case defendant address from the same court site (e.g., Hamilton County OH party page). Cheapest: no third-party cost.
2. **County assessor APN match** — match plaintiff/landlord name to assessor parcel data to infer the rental property address. Higher false-positive risk; requires single-match confidence score ≥ threshold.
3. **Alternative people search with address return** — use a higher-coverage API that returns a confirmed current address, then treat that as the property address for skip-trace. Higher per-call cost.

Once a source has a reliable street address, route through `enrich_tenant()` (BatchData skip-trace) instead of `enrich_tenant_by_name()`. Expected lift: 15% → ~50-60% phone hit rate.
