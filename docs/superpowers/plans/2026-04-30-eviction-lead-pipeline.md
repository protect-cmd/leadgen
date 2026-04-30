# Eviction Lead Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Python project foundation plus a working LA County Playwright scraper that runs the full pipeline (scrape → dedup → enrich stub → route → GHL stub → Bland stub) end-to-end.

**Architecture:** Cron-triggered Python service on Railway. Playwright scrapes LA Superior Court daily filings register, Supabase deduplicates, a pure-function router applies rent/property-type logic, and three stub services (BatchData, GHL, Bland) are wired with correct interfaces but raise `NotImplementedError` until credentials arrive. Runner catches `NotImplementedError` and logs warnings so the pipeline runs without crashing.

**Tech Stack:** Python 3.11+, Playwright (async), supabase-py 2.x (sync via asyncio.to_thread), httpx, Pydantic v2, pytest + pytest-asyncio, Railway cron, nixpacks

---

## File Map

| File | Status | Responsibility |
|---|---|---|
| `requirements.txt` | Create | Pinned dependencies |
| `nixpacks.toml` | Create | Chromium system deps + playwright install |
| `railway.toml` | Create | Cron schedule for CA job |
| `.env.example` | Create | All env var keys documented |
| `migrations/001_init.sql` | Create | Supabase table DDL |
| `models/filing.py` | Create | Pydantic Filing model |
| `models/contact.py` | Create | EnrichedContact + RoutingOutcome dataclasses |
| `scrapers/base_scraper.py` | Create | Abstract base with browser lifecycle |
| `scrapers/california/los_angeles.py` | Create | Real Playwright scraper for LA Superior Court |
| `scrapers/california/san_diego.py` | Create | NotImplementedError stub |
| `scrapers/california/orange.py` | Create | NotImplementedError stub |
| `scrapers/california/riverside.py` | Create | NotImplementedError stub |
| `services/dedup_service.py` | Create | Supabase check + insert + update helpers |
| `services/batchdata_service.py` | Create | NotImplementedError stub |
| `services/ghl_service.py` | Create | NotImplementedError stub |
| `services/bland_service.py` | Create | NotImplementedError stub |
| `pipeline/router.py` | Create | Pure routing function, zero external deps |
| `pipeline/runner.py` | Create | Orchestrates full pipeline per filing |
| `jobs/run_california.py` | Create | Railway cron entry point |
| `tests/test_router.py` | Create | All 5 routing branches |
| `tests/test_dedup_service.py` | Create | Duplicate detection integration test |
| `docs/portal_notes.md` | Create | LA selector map + SD/OC/RIV discovery checklist |

---

## Task 1: Project Foundation Files

**Files:**
- Create: `requirements.txt`
- Create: `nixpacks.toml`
- Create: `railway.toml`
- Create: `.env.example`

- [ ] **Step 1: Create `requirements.txt`**

```
playwright==1.44.0
httpx==0.27.0
pydantic==2.7.1
supabase==2.4.6
python-dotenv==1.0.1
pytest==8.2.0
pytest-asyncio==0.23.6
```

- [ ] **Step 2: Create `nixpacks.toml`**

```toml
[phases.setup]
nixPkgs = [
  "chromium",
  "chromium-driver",
  "glib",
  "nss",
  "nspr",
  "dbus",
  "atk",
  "cups",
  "libdrm",
  "libxkbcommon",
  "xorg.libX11",
  "xorg.libXcomposite",
  "xorg.libXdamage",
  "xorg.libXext",
  "xorg.libXfixes",
  "xorg.libXrandr",
  "xorg.libxcb",
  "mesa",
  "expat",
  "libxcb",
  "pango",
  "cairo",
  "alsa-lib"
]

[phases.install]
cmds = ["pip install -r requirements.txt", "playwright install chromium"]
```

- [ ] **Step 3: Create `railway.toml`**

```toml
[[cron]]
schedule = "0 6 * * *"
command = "python jobs/run_california.py"

# Uncomment as each county is implemented and tested:
# [[cron]]
# schedule = "0 6 * * *"
# command = "python jobs/run_texas.py"
```

- [ ] **Step 4: Create `.env.example`**

```bash
# BatchData
BATCHDATA_API_KEY=

# GHL
GHL_API_KEY=
GHL_LOCATION_ID=
GHL_NEW_FILING_STAGE_ID=
GHL_NG_COMMERCIAL_STAGE_ID=

# Bland.ai
BLAND_API_KEY=
BLAND_PHONE_NUMBER=

# Supabase
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=

# Service
ENVIRONMENT=production
LOG_LEVEL=INFO
```

- [ ] **Step 5: Install dependencies locally**

```bash
pip install -r requirements.txt
playwright install chromium
```

Expected: No errors. `playwright install chromium` downloads Chromium binary.

- [ ] **Step 6: Commit**

```bash
git init
git add requirements.txt nixpacks.toml railway.toml .env.example
git commit -m "feat: add project foundation and Railway config"
```

---

## Task 2: Database Migration

**Files:**
- Create: `migrations/001_init.sql`

- [ ] **Step 1: Create `migrations/001_init.sql`**

```sql
CREATE TABLE IF NOT EXISTS filings (
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

CREATE TABLE IF NOT EXISTS batchdata_cost_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_number     TEXT NOT NULL,
    called_at       TIMESTAMPTZ DEFAULT NOW(),
    cost_usd        NUMERIC(10,4) DEFAULT 0.07,
    phone_returned  BOOLEAN,
    email_returned  BOOLEAN
);
```

- [ ] **Step 2: Run migration against Supabase**

In the Supabase dashboard → SQL Editor, paste and run `migrations/001_init.sql`.

Expected: Both tables appear in the Table Editor with correct columns.

- [ ] **Step 3: Commit**

```bash
git add migrations/001_init.sql
git commit -m "feat: add Supabase schema migration for filings and cost log"
```

---

## Task 3: Data Models

**Files:**
- Create: `models/__init__.py`
- Create: `models/filing.py`
- Create: `models/contact.py`

- [ ] **Step 1: Create `models/__init__.py`** (empty)

```python
```

- [ ] **Step 2: Create `models/filing.py`**

```python
from __future__ import annotations
from datetime import date
from pydantic import BaseModel


class Filing(BaseModel):
    case_number: str
    tenant_name: str
    property_address: str
    landlord_name: str
    filing_date: date
    court_date: date | None = None
    state: str
    county: str
    notice_type: str
    source_url: str
```

- [ ] **Step 3: Create `models/contact.py`**

```python
from __future__ import annotations
from dataclasses import dataclass, field
from models.filing import Filing


@dataclass
class EnrichedContact:
    filing: Filing
    phone: str | None = None
    email: str | None = None
    secondary_address: str | None = None
    estimated_rent: float | None = None
    property_type: str | None = None  # "residential" | "commercial"


@dataclass
class RoutingOutcome:
    action: str        # "proceed" | "skip" | "flag"
    tag: str           # GHL tag to apply
    pipeline: str = "" # "residential" | "commercial" | ""
```

- [ ] **Step 4: Verify models import cleanly**

```bash
python -c "from models.filing import Filing; from models.contact import EnrichedContact, RoutingOutcome; print('OK')"
```

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add models/
git commit -m "feat: add Filing and EnrichedContact Pydantic/dataclass models"
```

---

## Task 4: Base Scraper

**Files:**
- Create: `scrapers/__init__.py`
- Create: `scrapers/california/__init__.py`
- Create: `scrapers/base_scraper.py`

- [ ] **Step 1: Create `scrapers/__init__.py` and `scrapers/california/__init__.py`** (both empty)

- [ ] **Step 2: Create `scrapers/base_scraper.py`**

```python
from __future__ import annotations
from abc import ABC, abstractmethod
from playwright.async_api import async_playwright, Browser, Page, Playwright
from models.filing import Filing


class BaseScraper(ABC):
    def __init__(self, headless: bool = True) -> None:
        self.headless = headless
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    @abstractmethod
    async def scrape(self) -> list[Filing]: ...

    async def _launch_browser(self) -> Page:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self.headless)
        context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        return await context.new_page()

    async def _close_browser(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
```

- [ ] **Step 3: Verify import**

```bash
python -c "from scrapers.base_scraper import BaseScraper; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add scrapers/
git commit -m "feat: add BaseScraper abstract class with Playwright browser lifecycle"
```

---

## Task 5: Router (TDD)

**Files:**
- Create: `pipeline/__init__.py`
- Create: `pipeline/router.py`
- Create: `tests/__init__.py`
- Create: `tests/test_router.py`

- [ ] **Step 1: Create `tests/__init__.py`** (empty) and `pipeline/__init__.py`** (empty)

- [ ] **Step 2: Write failing tests in `tests/test_router.py`**

```python
import pytest
from datetime import date
from models.filing import Filing
from models.contact import EnrichedContact, RoutingOutcome
from pipeline.router import route


def _make_contact(**kwargs) -> EnrichedContact:
    filing = Filing(
        case_number="TEST-001",
        tenant_name="Jane Doe",
        property_address="123 Main St, Los Angeles, CA 90001",
        landlord_name="ACME Properties",
        filing_date=date(2026, 4, 30),
        state="CA",
        county="Los Angeles",
        notice_type="Unlawful Detainer",
        source_url="https://www.lacourt.ca.gov",
    )
    defaults = dict(
        phone="5550001234",
        email="jane@example.com",
        secondary_address=None,
        estimated_rent=None,
        property_type=None,
    )
    defaults.update(kwargs)
    return EnrichedContact(filing=filing, **defaults)


def test_commercial_routes_to_ng():
    contact = _make_contact(property_type="commercial", estimated_rent=5000.0)
    outcome = route(contact)
    assert outcome.action == "proceed"
    assert outcome.tag == "NG-New-Filing"
    assert outcome.pipeline == "commercial"


def test_residential_above_threshold_routes_to_ec():
    contact = _make_contact(property_type="residential", estimated_rent=2000.0)
    outcome = route(contact)
    assert outcome.action == "proceed"
    assert outcome.tag == "EC-New-Filing"
    assert outcome.pipeline == "residential"


def test_residential_at_threshold_routes_to_ec():
    contact = _make_contact(property_type="residential", estimated_rent=1800.0)
    outcome = route(contact)
    assert outcome.action == "proceed"
    assert outcome.tag == "EC-New-Filing"


def test_residential_below_threshold_skipped():
    contact = _make_contact(property_type="residential", estimated_rent=1200.0)
    outcome = route(contact)
    assert outcome.action == "skip"
    assert outcome.tag == "Below-Threshold"


def test_missing_rent_flagged():
    contact = _make_contact(property_type="residential", estimated_rent=None)
    outcome = route(contact)
    assert outcome.action == "flag"
    assert outcome.tag == "Missing-Data"


def test_missing_property_type_flagged():
    contact = _make_contact(property_type=None, estimated_rent=2000.0)
    outcome = route(contact)
    assert outcome.action == "flag"
    assert outcome.tag == "Missing-Data"
```

- [ ] **Step 3: Run tests — verify they fail**

```bash
pytest tests/test_router.py -v
```

Expected: `ModuleNotFoundError: No module named 'pipeline.router'`

- [ ] **Step 4: Create `pipeline/router.py`**

```python
from models.contact import EnrichedContact, RoutingOutcome


def route(contact: EnrichedContact) -> RoutingOutcome:
    if contact.property_type == "commercial":
        return RoutingOutcome(action="proceed", tag="NG-New-Filing", pipeline="commercial")

    if contact.estimated_rent is None or contact.property_type is None:
        return RoutingOutcome(action="flag", tag="Missing-Data")

    if contact.estimated_rent < 1800:
        return RoutingOutcome(action="skip", tag="Below-Threshold")

    return RoutingOutcome(action="proceed", tag="EC-New-Filing", pipeline="residential")
```

- [ ] **Step 5: Run tests — verify all pass**

```bash
pytest tests/test_router.py -v
```

Expected:
```
test_commercial_routes_to_ng PASSED
test_residential_above_threshold_routes_to_ec PASSED
test_residential_at_threshold_routes_to_ec PASSED
test_residential_below_threshold_skipped PASSED
test_missing_rent_flagged PASSED
test_missing_property_type_flagged PASSED
6 passed
```

- [ ] **Step 6: Commit**

```bash
git add pipeline/ tests/
git commit -m "feat: add router with full branch coverage (TDD)"
```

---

## Task 6: Dedup Service (TDD)

**Files:**
- Create: `services/__init__.py`
- Create: `services/dedup_service.py`
- Create: `tests/test_dedup_service.py`

> **Prerequisite:** `.env` must have `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` filled in. The `filings` table must exist (Task 2 migration run).

- [ ] **Step 1: Create `services/__init__.py`** (empty)

- [ ] **Step 2: Write failing tests in `tests/test_dedup_service.py`**

```python
import pytest
import asyncio
from datetime import date
from models.filing import Filing
from services.dedup_service import is_duplicate, insert_filing, update_routing, update_ghl_id, mark_bland_triggered


TEST_CASE_NUMBER = "TEST-DEDUP-2026-001"

TEST_FILING = Filing(
    case_number=TEST_CASE_NUMBER,
    tenant_name="Test Tenant",
    property_address="999 Test St, Los Angeles, CA 90001",
    landlord_name="Test Landlord",
    filing_date=date(2026, 4, 30),
    state="CA",
    county="Los Angeles",
    notice_type="Unlawful Detainer",
    source_url="https://www.lacourt.ca.gov/test",
)


@pytest.fixture(autouse=True)
def cleanup():
    """Remove test row before and after each test."""
    from services.dedup_service import _client
    _client.table("filings").delete().eq("case_number", TEST_CASE_NUMBER).execute()
    yield
    _client.table("filings").delete().eq("case_number", TEST_CASE_NUMBER).execute()


def test_new_case_is_not_duplicate():
    result = asyncio.run(is_duplicate(TEST_CASE_NUMBER))
    assert result is False


def test_inserted_case_is_duplicate():
    asyncio.run(insert_filing(TEST_FILING))
    result = asyncio.run(is_duplicate(TEST_CASE_NUMBER))
    assert result is True


def test_update_routing_sets_outcome():
    from models.contact import RoutingOutcome
    asyncio.run(insert_filing(TEST_FILING))
    outcome = RoutingOutcome(action="proceed", tag="EC-New-Filing", pipeline="residential")
    asyncio.run(update_routing(TEST_CASE_NUMBER, outcome))
    from services.dedup_service import _client
    row = _client.table("filings").select("routing_outcome, routed").eq("case_number", TEST_CASE_NUMBER).execute()
    assert row.data[0]["routing_outcome"] == "proceed"
    assert row.data[0]["routed"] is True


def test_update_ghl_id():
    asyncio.run(insert_filing(TEST_FILING))
    asyncio.run(update_ghl_id(TEST_CASE_NUMBER, "ghl-123"))
    from services.dedup_service import _client
    row = _client.table("filings").select("ghl_contact_id").eq("case_number", TEST_CASE_NUMBER).execute()
    assert row.data[0]["ghl_contact_id"] == "ghl-123"


def test_mark_bland_triggered():
    asyncio.run(insert_filing(TEST_FILING))
    asyncio.run(mark_bland_triggered(TEST_CASE_NUMBER))
    from services.dedup_service import _client
    row = _client.table("filings").select("bland_triggered").eq("case_number", TEST_CASE_NUMBER).execute()
    assert row.data[0]["bland_triggered"] is True
```

- [ ] **Step 3: Run tests — verify they fail**

```bash
pytest tests/test_dedup_service.py -v
```

Expected: `ModuleNotFoundError: No module named 'services.dedup_service'`

- [ ] **Step 4: Create `services/dedup_service.py`**

```python
from __future__ import annotations
import asyncio
import os
from dotenv import load_dotenv
from supabase import create_client, Client
from models.filing import Filing
from models.contact import RoutingOutcome

load_dotenv()

_client: Client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"],
)


async def is_duplicate(case_number: str) -> bool:
    def _query() -> bool:
        result = _client.table("filings").select("case_number").eq("case_number", case_number).execute()
        return len(result.data) > 0
    return await asyncio.to_thread(_query)


async def insert_filing(filing: Filing) -> None:
    def _insert() -> None:
        _client.table("filings").insert({
            "case_number": filing.case_number,
            "tenant_name": filing.tenant_name,
            "property_address": filing.property_address,
            "landlord_name": filing.landlord_name,
            "filing_date": filing.filing_date.isoformat(),
            "court_date": filing.court_date.isoformat() if filing.court_date else None,
            "state": filing.state,
            "county": filing.county,
            "notice_type": filing.notice_type,
            "source_url": filing.source_url,
        }).execute()
    await asyncio.to_thread(_insert)


async def update_routing(case_number: str, outcome: RoutingOutcome) -> None:
    def _update() -> None:
        _client.table("filings").update({
            "routed": True,
            "routing_outcome": outcome.action,
        }).eq("case_number", case_number).execute()
    await asyncio.to_thread(_update)


async def update_ghl_id(case_number: str, ghl_contact_id: str) -> None:
    def _update() -> None:
        _client.table("filings").update({
            "ghl_contact_id": ghl_contact_id,
        }).eq("case_number", case_number).execute()
    await asyncio.to_thread(_update)


async def mark_bland_triggered(case_number: str) -> None:
    def _update() -> None:
        _client.table("filings").update({
            "bland_triggered": True,
        }).eq("case_number", case_number).execute()
    await asyncio.to_thread(_update)
```

- [ ] **Step 5: Run tests — verify all pass**

```bash
pytest tests/test_dedup_service.py -v
```

Expected:
```
test_new_case_is_not_duplicate PASSED
test_inserted_case_is_duplicate PASSED
test_update_routing_sets_outcome PASSED
test_update_ghl_id PASSED
test_mark_bland_triggered PASSED
5 passed
```

- [ ] **Step 6: Commit**

```bash
git add services/dedup_service.py tests/test_dedup_service.py
git commit -m "feat: add dedup service with Supabase integration (TDD)"
```

---

## Task 7: Service Stubs (BatchData, GHL, Bland)

**Files:**
- Create: `services/batchdata_service.py`
- Create: `services/ghl_service.py`
- Create: `services/bland_service.py`

- [ ] **Step 1: Create `services/batchdata_service.py`**

```python
from __future__ import annotations
from models.filing import Filing
from models.contact import EnrichedContact


async def enrich(filing: Filing) -> EnrichedContact:
    raise NotImplementedError(
        "BatchData credentials not yet received. "
        "See open items in eviction-lead-pipeline-CLAUDE.md."
    )
```

- [ ] **Step 2: Create `services/ghl_service.py`**

```python
from __future__ import annotations
from models.contact import EnrichedContact


async def create_contact(
    contact: EnrichedContact,
    tags: list[str],
    pipeline_stage_id: str,
) -> str:
    raise NotImplementedError(
        "GHL stage IDs and custom field IDs not yet received. "
        "See open items in eviction-lead-pipeline-CLAUDE.md."
    )
```

- [ ] **Step 3: Create `services/bland_service.py`**

```python
from __future__ import annotations


async def trigger_voicemail(
    phone: str,
    tenant_name: str,
    property_address: str,
) -> None:
    raise NotImplementedError(
        "Bland agent IDs and outbound phone number not yet received. "
        "See open items in eviction-lead-pipeline-CLAUDE.md."
    )
```

- [ ] **Step 4: Verify all stubs import cleanly**

```bash
python -c "
from services.batchdata_service import enrich
from services.ghl_service import create_contact
from services.bland_service import trigger_voicemail
print('OK')
"
```

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add services/batchdata_service.py services/ghl_service.py services/bland_service.py
git commit -m "feat: add BatchData, GHL, Bland service stubs with correct interfaces"
```

---

## Task 8: Pipeline Runner

**Files:**
- Create: `pipeline/runner.py`

- [ ] **Step 1: Create `pipeline/runner.py`**

```python
from __future__ import annotations
import logging
import os
from models.filing import Filing
from models.contact import EnrichedContact
from pipeline import router
from services import dedup_service, batchdata_service, ghl_service, bland_service

log = logging.getLogger(__name__)

GHL_NEW_FILING_STAGE_ID = os.getenv("GHL_NEW_FILING_STAGE_ID", "")
GHL_NG_COMMERCIAL_STAGE_ID = os.getenv("GHL_NG_COMMERCIAL_STAGE_ID", "")


async def run(filings: list[Filing]) -> None:
    log.info(f"Runner received {len(filings)} filings")

    for filing in filings:
        log.info(f"Processing {filing.case_number}")

        if await dedup_service.is_duplicate(filing.case_number):
            log.info(f"Duplicate — skipping: {filing.case_number}")
            continue

        await dedup_service.insert_filing(filing)

        try:
            contact: EnrichedContact = await batchdata_service.enrich(filing)
        except NotImplementedError as e:
            log.warning(f"BatchData not implemented — skipping enrichment: {e}")
            continue

        outcome = router.route(contact)
        await dedup_service.update_routing(filing.case_number, outcome)
        log.info(f"{filing.case_number} routed: action={outcome.action} tag={outcome.tag}")

        if outcome.action != "proceed":
            continue

        stage_id = (
            GHL_NG_COMMERCIAL_STAGE_ID
            if outcome.pipeline == "commercial"
            else GHL_NEW_FILING_STAGE_ID
        )

        try:
            ghl_id = await ghl_service.create_contact(contact, [outcome.tag], stage_id)
            await dedup_service.update_ghl_id(filing.case_number, ghl_id)
            log.info(f"GHL contact created: {ghl_id}")
        except NotImplementedError as e:
            log.warning(f"GHL not implemented — skipping contact creation: {e}")
            continue

        if contact.phone:
            try:
                await bland_service.trigger_voicemail(
                    contact.phone,
                    filing.tenant_name,
                    filing.property_address,
                )
                await dedup_service.mark_bland_triggered(filing.case_number)
                log.info(f"Bland voicemail triggered for {filing.case_number}")
            except NotImplementedError as e:
                log.warning(f"Bland not implemented — skipping voicemail: {e}")
```

- [ ] **Step 2: Verify import**

```bash
python -c "from pipeline.runner import run; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add pipeline/runner.py
git commit -m "feat: add pipeline runner orchestrating scrape→dedup→enrich→route→GHL→Bland"
```

---

## Task 9: LA County Portal Discovery

**Files:**
- Create: `docs/portal_notes.md` (initial skeleton)

> This task is manual. You run the browser in **headed mode** and map the LA Superior Court portal before writing selectors into the scraper.

- [ ] **Step 1: Create `docs/portal_notes.md` with discovery checklist**

```markdown
# Court Portal Notes

## LA Superior Court — lacourt.ca.gov

**Status:** In discovery  
**Target:** Daily new filings register filtered to Unlawful Detainer

### Discovery Steps (run once, headed mode)
1. Navigate to https://www.lacourt.ca.gov/newfilings/ui/index.aspx
2. Identify: Does a "Case Type" or "Category" dropdown exist? What are its options?
3. Identify: Is there a date filter? Default to today?
4. Identify: CSS selector for each result row
5. Identify: Fields visible in the results list (case number, parties, address, filing date)
6. Click one result row — identify the case detail page URL pattern
7. On case detail page: identify selectors for court_date, landlord_name
8. Identify: Pagination — next button selector, total pages indicator
9. Note: Any CAPTCHA, rate limiting, or session token requirements?

### Confirmed Selectors (fill in after discovery)
| Field | Selector | Notes |
|---|---|---|
| Case type dropdown | TBD | |
| Date filter | TBD | |
| Result rows | TBD | |
| Case number in row | TBD | |
| Tenant name in row | TBD | |
| Property address in row | TBD | |
| Filing date in row | TBD | |
| Next page button | TBD | |
| Case detail: court date | TBD | |
| Case detail: landlord name | TBD | |

---

## San Diego Superior Court — sdcourt.ca.gov
**Status:** Not yet discovered  
**Discovery checklist:** Same 9 steps as LA above.

---

## Orange County Superior Court — occourts.org
**Status:** Not yet discovered

---

## Riverside Superior Court — riverside.courts.ca.gov
**Status:** Not yet discovered
```

- [ ] **Step 2: Run browser in headed mode to discover LA portal**

Create a temporary discovery script `scratch_la_discover.py` (do not commit):

```python
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=500)
        page = await browser.new_page()
        await page.goto("https://www.lacourt.ca.gov/newfilings/ui/index.aspx")
        input("Explore the page. Press Enter when done...")
        await browser.close()

asyncio.run(main())
```

Run it:
```bash
python scratch_la_discover.py
```

In the browser window: explore the page, use DevTools (F12) to identify selectors for each field in the table above. Fill in `docs/portal_notes.md` with confirmed selectors before proceeding to Task 10.

- [ ] **Step 3: Commit portal notes**

```bash
git add docs/portal_notes.md
git commit -m "docs: add portal notes with LA discovery results and SD/OC/RIV checklists"
```

---

## Task 10: LA County Scraper

**Files:**
- Create: `scrapers/california/los_angeles.py`

> **Prerequisite:** Task 9 portal discovery complete. `docs/portal_notes.md` has all LA selectors filled in.

Replace every `SELECTOR_*` constant below with the confirmed selectors from `docs/portal_notes.md`.

- [ ] **Step 1: Create `scrapers/california/los_angeles.py`**

```python
from __future__ import annotations
import logging
from datetime import date, datetime
from scrapers.base_scraper import BaseScraper
from models.filing import Filing

log = logging.getLogger(__name__)

PORTAL_URL = "https://www.lacourt.ca.gov/newfilings/ui/index.aspx"
SOURCE_URL = PORTAL_URL

# Replace with confirmed selectors from docs/portal_notes.md after portal discovery
SELECTOR_CASE_TYPE_DROPDOWN = ""   # e.g. "select#caseType"
SELECTOR_CASE_TYPE_UD_OPTION = ""  # e.g. "Unlawful Detainer"
SELECTOR_DATE_INPUT = ""           # e.g. "input#filingDate" — leave blank if defaults to today
SELECTOR_RESULT_ROWS = ""          # e.g. "table.results tbody tr"
SELECTOR_ROW_CASE_NUMBER = ""      # e.g. "td:nth-child(1) a"
SELECTOR_ROW_TENANT_NAME = ""      # e.g. "td:nth-child(2)"
SELECTOR_ROW_ADDRESS = ""          # e.g. "td:nth-child(3)"
SELECTOR_ROW_FILING_DATE = ""      # e.g. "td:nth-child(4)"
SELECTOR_NEXT_PAGE = ""            # e.g. "a.next-page"
SELECTOR_DETAIL_COURT_DATE = ""    # e.g. "#courtDate"
SELECTOR_DETAIL_LANDLORD = ""      # e.g. "#plaintiffName"

NOTICE_TYPE = "Unlawful Detainer"
STATE = "CA"
COUNTY = "Los Angeles"


class LosAngelesScraper(BaseScraper):

    async def scrape(self) -> list[Filing]:
        page = await self._launch_browser()
        filings: list[Filing] = []

        try:
            await page.goto(PORTAL_URL, wait_until="networkidle")

            if SELECTOR_CASE_TYPE_DROPDOWN:
                await page.select_option(SELECTOR_CASE_TYPE_DROPDOWN, label=SELECTOR_CASE_TYPE_UD_OPTION)
                await page.wait_for_load_state("networkidle")

            while True:
                rows = await page.query_selector_all(SELECTOR_RESULT_ROWS)
                if not rows:
                    log.info("No result rows found on current page")
                    break

                for row in rows:
                    try:
                        case_number = await self._text(row, SELECTOR_ROW_CASE_NUMBER)
                        tenant_name = await self._text(row, SELECTOR_ROW_TENANT_NAME)
                        address = await self._text(row, SELECTOR_ROW_ADDRESS)
                        filing_date_raw = await self._text(row, SELECTOR_ROW_FILING_DATE)
                        filing_date = self._parse_date(filing_date_raw)

                        detail_url = await self._href(row, SELECTOR_ROW_CASE_NUMBER)
                        court_date, landlord_name = await self._fetch_detail(page, detail_url)

                        filings.append(Filing(
                            case_number=case_number.strip(),
                            tenant_name=tenant_name.strip(),
                            property_address=address.strip(),
                            landlord_name=landlord_name.strip(),
                            filing_date=filing_date,
                            court_date=court_date,
                            state=STATE,
                            county=COUNTY,
                            notice_type=NOTICE_TYPE,
                            source_url=detail_url or SOURCE_URL,
                        ))
                    except Exception as e:
                        log.warning(f"Failed to parse row: {e}")
                        continue

                next_btn = await page.query_selector(SELECTOR_NEXT_PAGE)
                if not next_btn:
                    break
                await next_btn.click()
                await page.wait_for_load_state("networkidle")

        finally:
            await self._close_browser()

        log.info(f"LA scraper returned {len(filings)} filings")
        return filings

    async def _fetch_detail(self, page, url: str) -> tuple[date | None, str]:
        if not url:
            return None, ""
        await page.goto(url, wait_until="networkidle")
        court_date_raw = await self._text(page, SELECTOR_DETAIL_COURT_DATE, default="")
        landlord_raw = await self._text(page, SELECTOR_DETAIL_LANDLORD, default="")
        court_date = self._parse_date(court_date_raw) if court_date_raw else None
        await page.go_back(wait_until="networkidle")
        return court_date, landlord_raw

    @staticmethod
    async def _text(element, selector: str, default: str = "") -> str:
        el = await element.query_selector(selector)
        if not el:
            return default
        return (await el.inner_text()).strip()

    @staticmethod
    async def _href(element, selector: str) -> str:
        el = await element.query_selector(selector)
        if not el:
            return ""
        return await el.get_attribute("href") or ""

    @staticmethod
    def _parse_date(raw: str) -> date:
        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
            try:
                return datetime.strptime(raw.strip(), fmt).date()
            except ValueError:
                continue
        raise ValueError(f"Cannot parse date: {raw!r}")
```

- [ ] **Step 2: Verify import**

```bash
python -c "from scrapers.california.los_angeles import LosAngelesScraper; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Run scraper in headed mode against live portal**

```bash
python -c "
import asyncio
from scrapers.california.los_angeles import LosAngelesScraper

async def main():
    scraper = LosAngelesScraper(headless=False)
    filings = await scraper.scrape()
    for f in filings[:3]:
        print(f)

asyncio.run(main())
"
```

Expected: 3 `Filing` objects printed with real case numbers, tenant names, and addresses. If selectors are wrong, update constants and re-run until output is correct.

- [ ] **Step 4: Update `docs/portal_notes.md` with final confirmed selectors**

Fill in the confirmed selector values in `docs/portal_notes.md`.

- [ ] **Step 5: Commit**

```bash
git add scrapers/california/los_angeles.py docs/portal_notes.md
git commit -m "feat: implement LA County Playwright scraper for Unlawful Detainer filings"
```

---

## Task 11: California County Stubs

**Files:**
- Create: `scrapers/california/san_diego.py`
- Create: `scrapers/california/orange.py`
- Create: `scrapers/california/riverside.py`

- [ ] **Step 1: Create `scrapers/california/san_diego.py`**

```python
from scrapers.base_scraper import BaseScraper
from models.filing import Filing


class SanDiegoScraper(BaseScraper):
    async def scrape(self) -> list[Filing]:
        raise NotImplementedError(
            "San Diego Superior Court portal selectors not yet mapped. "
            "See docs/portal_notes.md for discovery checklist."
        )
```

- [ ] **Step 2: Create `scrapers/california/orange.py`**

```python
from scrapers.base_scraper import BaseScraper
from models.filing import Filing


class OrangeCountyScraper(BaseScraper):
    async def scrape(self) -> list[Filing]:
        raise NotImplementedError(
            "Orange County Superior Court portal selectors not yet mapped. "
            "See docs/portal_notes.md for discovery checklist."
        )
```

- [ ] **Step 3: Create `scrapers/california/riverside.py`**

```python
from scrapers.base_scraper import BaseScraper
from models.filing import Filing


class RiversideScraper(BaseScraper):
    async def scrape(self) -> list[Filing]:
        raise NotImplementedError(
            "Riverside Superior Court portal selectors not yet mapped. "
            "See docs/portal_notes.md for discovery checklist."
        )
```

- [ ] **Step 4: Commit**

```bash
git add scrapers/california/san_diego.py scrapers/california/orange.py scrapers/california/riverside.py
git commit -m "feat: add San Diego, Orange, Riverside scraper stubs"
```

---

## Task 12: Job Entry Point

**Files:**
- Create: `jobs/__init__.py`
- Create: `jobs/run_california.py`

- [ ] **Step 1: Create `jobs/__init__.py`** (empty)

- [ ] **Step 2: Create `jobs/run_california.py`**

```python
from __future__ import annotations
import asyncio
import logging
import os
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

log = logging.getLogger(__name__)


async def main() -> None:
    from scrapers.california.los_angeles import LosAngelesScraper
    from pipeline.runner import run

    log.info("Starting California scrape job")

    scrapers = [
        ("Los Angeles", LosAngelesScraper()),
    ]

    for county, scraper in scrapers:
        log.info(f"Scraping {county} County")
        try:
            filings = await scraper.scrape()
            log.info(f"{county}: {len(filings)} filings scraped")
            await run(filings)
        except NotImplementedError as e:
            log.warning(f"{county} scraper not yet implemented: {e}")
        except Exception as e:
            log.error(f"{county} scrape failed: {e}", exc_info=True)

    log.info("California scrape job complete")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 3: Verify the job runs end-to-end (stubs allowed)**

```bash
python jobs/run_california.py
```

Expected log output:
```
Starting California scrape job
Scraping Los Angeles County
LA scraper returned N filings
Runner received N filings
BatchData not implemented — skipping enrichment: ...   ← expected
California scrape job complete
```

No crashes. Dedup rows written to Supabase for each scraped filing.

- [ ] **Step 4: Verify Supabase has rows**

In Supabase dashboard → Table Editor → `filings`: confirm rows from today's LA scrape are present with `enriched=false`, `routed=false`.

- [ ] **Step 5: Commit**

```bash
git add jobs/
git commit -m "feat: add run_california.py job entry point with full pipeline wiring"
```

---

## Task 13: Full Test Suite Pass

- [ ] **Step 1: Run all tests**

```bash
pytest tests/ -v
```

Expected:
```
tests/test_router.py::test_commercial_routes_to_ng PASSED
tests/test_router.py::test_residential_above_threshold_routes_to_ec PASSED
tests/test_router.py::test_residential_at_threshold_routes_to_ec PASSED
tests/test_router.py::test_residential_below_threshold_skipped PASSED
tests/test_router.py::test_missing_rent_flagged PASSED
tests/test_router.py::test_missing_property_type_flagged PASSED
tests/test_dedup_service.py::test_new_case_is_not_duplicate PASSED
tests/test_dedup_service.py::test_inserted_case_is_duplicate PASSED
tests/test_dedup_service.py::test_update_routing_sets_outcome PASSED
tests/test_dedup_service.py::test_update_ghl_id PASSED
tests/test_dedup_service.py::test_mark_bland_triggered PASSED
11 passed
```

- [ ] **Step 2: Final commit**

```bash
git add .
git commit -m "chore: verify full test suite passes before first Railway deploy"
```

---

## Open Items Checklist (Not In This Plan)

When each item is resolved, implement the corresponding service:

| Item | Unblocks |
|---|---|
| BatchData API credentials | `services/batchdata_service.py` real implementation |
| GHL stage IDs + custom field IDs | `services/ghl_service.py` real implementation |
| Bland agent IDs + outbound number | `services/bland_service.py` real implementation |
| San Diego portal selectors | `scrapers/california/san_diego.py` |
| Orange County portal selectors | `scrapers/california/orange.py` |
| Riverside portal selectors | `scrapers/california/riverside.py` |
