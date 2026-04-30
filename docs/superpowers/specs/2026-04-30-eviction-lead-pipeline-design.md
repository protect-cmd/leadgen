# Eviction Lead Pipeline — Foundation + California Scraper Design
**Date:** 2026-04-30  
**Status:** Approved  
**Scope:** Python project foundation + LA County Playwright scraper + full pipeline skeleton

---

## 1. Goal

Stand up the complete eviction lead pipeline project with:
- Working Python foundation (all boilerplate, config, migrations)
- LA County Superior Court Playwright scraper producing real `Filing` objects
- Dedup service wired to Supabase (real)
- Router with full routing logic (real, unit-testable)
- BatchData, GHL, Bland services as interface-correct stubs (real signatures, `NotImplementedError` bodies)
- End-to-end runner that executes LA → dedup → enrich (stub) → route → push (stub) without crashing
- Railway cron + nixpacks config ready to deploy

San Diego, Orange, and Riverside scrapers are `NotImplementedError` stubs pending manual portal discovery.

---

## 2. Approach

**Approach C — Foundation + LA-first build.**

LA Superior Court publishes a daily new filing register scrapeable with Playwright. All other counties are stubbed with `NotImplementedError` and a portal discovery checklist. Services blocked on credentials (BatchData, GHL, Bland) are stubbed the same way — correct interfaces, no implementation — so the runner can call them without crashing.

This matches the spec's "do not move to the next item until the current one is confirmed working end-to-end" build philosophy.

---

## 3. Project Structure

```
eviction-lead-pipeline/
├── eviction-lead-pipeline-CLAUDE.md
├── requirements.txt
├── railway.toml
├── nixpacks.toml
├── .env.example
├── migrations/
│   └── 001_init.sql
├── models/
│   └── filing.py
├── scrapers/
│   ├── base_scraper.py
│   └── california/
│       ├── los_angeles.py       ← real Playwright implementation
│       ├── san_diego.py         ← NotImplementedError stub
│       ├── orange.py            ← NotImplementedError stub
│       └── riverside.py         ← NotImplementedError stub
├── services/
│   ├── dedup_service.py         ← real (Supabase)
│   ├── batchdata_service.py     ← stub
│   ├── ghl_service.py           ← stub
│   └── bland_service.py         ← stub
├── pipeline/
│   ├── router.py                ← real routing logic
│   └── runner.py                ← real orchestrator
├── jobs/
│   └── run_california.py
├── tests/
│   ├── test_router.py
│   └── test_dedup_service.py
└── docs/
    ├── portal_notes.md
    └── superpowers/specs/
        └── 2026-04-30-eviction-lead-pipeline-design.md
```

---

## 4. Data Model

### `models/filing.py`

```python
from pydantic import BaseModel
from datetime import date

class Filing(BaseModel):
    case_number: str
    tenant_name: str
    property_address: str
    landlord_name: str
    filing_date: date
    court_date: date | None = None
    state: str          # "CA"
    county: str         # "Los Angeles"
    notice_type: str    # raw string from portal e.g. "Unlawful Detainer"
    source_url: str
```

### `models/contact.py`

```python
from dataclasses import dataclass

@dataclass
class EnrichedContact:
    filing: Filing
    phone: str | None
    email: str | None
    secondary_address: str | None
    estimated_rent: float | None
    property_type: str | None   # "residential" | "commercial" | None
```

`EnrichedContact` is not persisted directly — it is the in-memory output of BatchData enrichment, consumed by the router and GHL service.

---

## 5. Base Scraper

```python
# scrapers/base_scraper.py
from abc import ABC, abstractmethod
from playwright.async_api import async_playwright, Browser, Page
from models.filing import Filing

class BaseScraper(ABC):
    def __init__(self, headless: bool = True):
        self.headless = headless
        self._browser: Browser | None = None
        self._page: Page | None = None

    @abstractmethod
    async def scrape(self) -> list[Filing]: ...

    async def _launch_browser(self) -> Page: ...
    async def _close_browser(self) -> None: ...
```

All county scrapers inherit `BaseScraper` and implement only `scrape()`. Browser lifecycle is owned by the base class.

---

## 6. LA County Scraper

**Portal:** LA Superior Court daily new filing register (`lacourt.ca.gov`)  
**Case type filter:** Unlawful Detainer  
**Flow:**

1. Navigate to daily register page
2. Filter by case type → "Unlawful Detainer"
3. Iterate result rows → extract `case_number`, `tenant_name`, `property_address`, `filing_date`
4. For each case: navigate to case detail page → extract `court_date`, `landlord_name`
5. Return `list[Filing]`

**Selectors:** Discovered at implementation time using Playwright's browser. Documented in `docs/portal_notes.md` after first successful run.

**Stubs for other CA counties:**

```python
class SanDiegoScraper(BaseScraper):
    async def scrape(self) -> list[Filing]:
        raise NotImplementedError(
            "San Diego portal selectors not yet mapped. "
            "See docs/portal_notes.md for discovery checklist."
        )
```

Same pattern for Orange and Riverside.

---

## 7. Services

### Dedup Service — Real

```python
async def is_duplicate(case_number: str) -> bool:
    # queries Supabase filings table
    ...

async def insert_filing(filing: Filing) -> None:
    # inserts before enrichment to prevent race conditions
    ...
```

### BatchData Service — Stub

```python
async def enrich(filing: Filing) -> EnrichedContact:
    raise NotImplementedError("Awaiting BatchData credentials")
```

### GHL Service — Stub

```python
async def create_contact(
    contact: EnrichedContact,
    tags: list[str],
    pipeline_stage_id: str
) -> str:
    raise NotImplementedError("Awaiting GHL stage IDs and custom field IDs")
```

### Bland Service — Stub

```python
async def trigger_voicemail(
    phone: str,
    tenant_name: str,
    property_address: str
) -> None:
    raise NotImplementedError("Awaiting Bland agent IDs and outbound number")
```

---

## 8. Router

Pure function, zero external dependencies, fully unit-testable.

```python
def route(contact: EnrichedContact) -> RoutingOutcome:
    if contact.property_type == "commercial":
        return RoutingOutcome(action="proceed", tag="NG-New-Filing", pipeline="commercial")
    if contact.estimated_rent is None or contact.property_type is None:
        return RoutingOutcome(action="flag", tag="Missing-Data")
    if contact.estimated_rent < 1800:
        return RoutingOutcome(action="skip", tag="Below-Threshold")
    return RoutingOutcome(action="proceed", tag="EC-New-Filing", pipeline="residential")
```

`RoutingOutcome` is a small dataclass with `action`, `tag`, `pipeline` fields.

---

## 9. Pipeline Runner

```python
async def run(filings: list[Filing]) -> None:
    for filing in filings:
        if await dedup_service.is_duplicate(filing.case_number):
            log.info(f"Duplicate skipped: {filing.case_number}")
            continue

        await dedup_service.insert_filing(filing)

        try:
            contact = await batchdata_service.enrich(filing)
        except NotImplementedError:
            log.warning("BatchData not implemented — skipping enrichment")
            continue

        outcome = router.route(contact)

        # update Supabase routing_outcome regardless
        await dedup_service.update_routing(filing.case_number, outcome)

        if outcome.action != "proceed":
            continue

        stage_id = (
            GHL_NG_COMMERCIAL_STAGE_ID if outcome.pipeline == "commercial"
            else GHL_NEW_FILING_STAGE_ID
        )

        try:
            ghl_id = await ghl_service.create_contact(contact, [outcome.tag], stage_id)
            await dedup_service.update_ghl_id(filing.case_number, ghl_id)
        except NotImplementedError:
            log.warning("GHL not implemented — skipping contact creation")
            continue

        if contact.phone:
            try:
                await bland_service.trigger_voicemail(
                    contact.phone, filing.tenant_name, filing.property_address
                )
                await dedup_service.mark_bland_triggered(filing.case_number)
            except NotImplementedError:
                log.warning("Bland not implemented — skipping voicemail")
```

Runner logs at each step. `NotImplementedError` from any stub is caught and logged — pipeline does not crash.

---

## 10. Database Schema

### `migrations/001_init.sql`

```sql
CREATE TABLE filings (
    case_number        TEXT PRIMARY KEY,
    tenant_name        TEXT NOT NULL,
    property_address   TEXT NOT NULL,
    landlord_name      TEXT NOT NULL,
    filing_date        DATE NOT NULL,
    court_date         DATE,
    state              TEXT NOT NULL,
    county             TEXT NOT NULL,
    notice_type        TEXT NOT NULL,
    source_url         TEXT NOT NULL,
    scraped_at         TIMESTAMPTZ DEFAULT NOW(),
    enriched           BOOLEAN DEFAULT FALSE,
    enriched_at        TIMESTAMPTZ,
    routed             BOOLEAN DEFAULT FALSE,
    routing_outcome    TEXT,
    ghl_contact_id     TEXT,
    bland_triggered    BOOLEAN DEFAULT FALSE
);

CREATE TABLE batchdata_cost_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_number     TEXT NOT NULL,
    called_at       TIMESTAMPTZ DEFAULT NOW(),
    cost_usd        NUMERIC(10,4) DEFAULT 0.07,
    phone_returned  BOOLEAN,
    email_returned  BOOLEAN
);
```

Run manually against Supabase once before first deploy.

---

## 11. Infrastructure

### `requirements.txt`
```
playwright==1.44.0
httpx==0.27.0
pydantic==2.7.1
supabase==2.4.6
python-dotenv==1.0.1
pytest==8.2.0
pytest-asyncio==0.23.6
```

### `railway.toml`
```toml
[[cron]]
schedule = "0 6 * * *"
command = "python jobs/run_california.py"
```

### `nixpacks.toml`
Full Chromium system dep list from spec. Installs `playwright install chromium` during build.

### `.env.example`
All env var keys from spec, no values. Supabase URL + service role key required to run dedup service. All others optional until stubs are replaced.

---

## 12. Tests

### `tests/test_router.py` — all branches covered
- commercial → NG-New-Filing, proceed
- residential ≥ $1,800 → EC-New-Filing, proceed
- residential < $1,800 → Below-Threshold, skip
- missing estimated_rent → Missing-Data, flag
- missing property_type → Missing-Data, flag

### `tests/test_dedup_service.py`
- new case_number → `is_duplicate` returns False, `insert_filing` writes row
- duplicate case_number → `is_duplicate` returns True

---

## 13. Open Items (Do Not Build Until Resolved)

| Item | Blocked On |
|---|---|
| BatchData service implementation | Credentials from Sunshine |
| GHL service implementation | Stage IDs + custom field IDs from Zee |
| Bland service implementation | Agent IDs + outbound number from Zee |
| San Diego scraper selectors | Manual portal discovery |
| Orange County scraper selectors | Manual portal discovery |
| Riverside scraper selectors | Manual portal discovery |
| `estimated_rent` / `property_type` from BatchData | Verify with credentials |

---

## 14. Build Sequence (This Session)

1. `requirements.txt`, `railway.toml`, `nixpacks.toml`, `.env.example`
2. `migrations/001_init.sql`
3. `models/filing.py`, `models/contact.py`
4. `scrapers/base_scraper.py`
5. `scrapers/california/los_angeles.py` — real Playwright (Context7 for current API)
6. `scrapers/california/{san_diego,orange,riverside}.py` — stubs
7. `services/dedup_service.py` — real Supabase (Context7 for supabase-py)
8. `services/{batchdata,ghl,bland}_service.py` — stubs
9. `pipeline/router.py` + `pipeline/runner.py`
10. `jobs/run_california.py`
11. `tests/test_router.py` + `tests/test_dedup_service.py`
12. `docs/portal_notes.md`
