# Eviction Lead Pipeline — Automation Service
## CLAUDE.md — Living Project Context

> This is a separate service from the Grant Ellis Group document generator. Do not mix concerns.
> Update this file whenever architecture decisions change or open items are resolved.

---

## What This Is

A Python automation service hosted on Railway that replaces Make.com as the lead acquisition pipeline for Grant Ellis Group (and Vantage Defense Group). It:

1. Scrapes public court portals daily for new eviction filings
2. Deduplicates filings against Supabase to avoid reprocessing
3. Enriches each filing via BatchData (phone, email, secondary address)
4. Routes contacts based on rent threshold and property type
5. Creates contacts in GHL with all enriched fields + correct tags
6. Triggers Bland.ai voicemail drop immediately
7. GHL's native workflows take over for SMS + follow-up sequences

This service is **proactive and scheduled**, not webhook-triggered. It runs on a cron schedule and fires outbound.

---

## Relationship to Other Services

| Service | Repo | Purpose |
|---|---|---|
| This service | eviction-lead-pipeline | Scrape → Enrich → Route → GHL |
| Document generator | reportgenerator | GHL webhook → PDF → Drive |

These are **fully independent Railway projects**. They share only the GHL API integration pattern (copy the utility, don't import across services).

---

## Tech Stack

| Layer | Tool | Notes |
|---|---|---|
| Language | Python 3.11+ | |
| Scraping | Playwright (async) | Headless Chromium. One scraper class per state. |
| Scheduling | Railway Cron | One cron job per state scraper, 6 AM daily |
| HTTP client | httpx (async) | All external API calls (BatchData, GHL, Bland.ai) |
| Database | Supabase (Postgres) | Stores raw filings, enriched contacts, cost log |
| ORM | supabase-py | Official Python client |
| Hosting | Railway | Separate project from document generator |
| Config | python-dotenv | |

**No FastAPI needed** — this service has no inbound HTTP endpoints. It is entirely outbound and schedule-driven. If a health check endpoint is needed later, add a minimal one.

---

## Architecture

```
Railway Cron (6 AM daily)
        ↓
  Scraper (per state/county)          ← Playwright, headless Chromium
        ↓
  Dedup check (Supabase)              ← skip if case_number already exists
        ↓
  BatchData enrichment                ← httpx POST, log cost to Supabase
        ↓
  Routing logic                       ← rent threshold + property type
        ↓
  GHL API                             ← create contact, set tags, set pipeline stage
        ↓
  Bland.ai API                        ← voicemail drop (if phone available)
        ↓
  GHL Workflow takes over             ← SMS at 2hr, follow-up sequences (built in GHL)
```

---

## Project Structure

```
eviction-lead-pipeline/
├── CLAUDE.md                          ← this file
├── requirements.txt
├── railway.toml
├── nixpacks.toml                      ← Playwright + Chromium system deps
├── .env.example
├── .env                               ← local only, never commit
├── scrapers/
│   ├── base_scraper.py                ← abstract base class all scrapers inherit
│   ├── california/
│   │   ├── los_angeles.py
│   │   ├── san_diego.py
│   │   ├── orange.py
│   │   └── riverside.py
│   ├── texas/
│   │   ├── harris.py                  ← jp.hctx.net
│   │   ├── dallas.py
│   │   ├── bexar.py
│   │   └── travis.py
│   ├── illinois/
│   │   └── cook.py
│   ├── florida/
│   │   ├── miami_dade.py
│   │   ├── broward.py
│   │   └── hillsborough.py
│   ├── washington/
│   │   └── king.py
│   ├── arizona/
│   │   └── maricopa.py
│   ├── nevada/
│   │   └── clark.py
│   └── georgia/
│       └── fulton.py                  ← filter: "Dispossessory" NOT "Unlawful Detainer"
├── services/
│   ├── batchdata_service.py           ← enrich contact, log cost
│   ├── ghl_service.py                 ← create contact, set tags, set pipeline stage
│   ├── bland_service.py               ← trigger voicemail drop
│   └── dedup_service.py               ← check + insert case_number in Supabase
├── pipeline/
│   ├── router.py                      ← rent threshold + property type routing logic
│   └── runner.py                      ← orchestrates scrape → enrich → route → push
├── models/
│   └── filing.py                      ← Pydantic model for a court filing
├── jobs/
│   ├── run_california.py              ← entry point called by Railway Cron
│   ├── run_texas.py
│   ├── run_illinois.py
│   ├── run_florida.py
│   ├── run_washington.py
│   ├── run_arizona.py
│   ├── run_nevada.py
│   └── run_georgia.py
├── tests/
│   ├── test_scrapers.py
│   ├── test_batchdata_service.py
│   ├── test_ghl_service.py
│   ├── test_router.py
│   └── test_dedup_service.py
└── docs/
    └── portal_notes.md               ← per-portal quirks, selector notes, login requirements
```

---

## Scraper Details

### Base Scraper Contract

Every scraper inherits `BaseScraper` and implements one method:

```python
async def scrape() -> list[Filing]
```

`Filing` is the Pydantic model defined in `models/filing.py`. All scrapers must return the same shape.

### Fields to Extract (All States)

| Field | Required | Notes |
|---|---|---|
| `case_number` | Yes | Primary dedup key |
| `tenant_name` | Yes | Full legal name |
| `property_address` | Yes | |
| `landlord_name` | Yes | |
| `filing_date` | Yes | ISO format |
| `court_date` | No | None if not yet scheduled |
| `state` | Yes | Two-letter code, e.g. "CA" |
| `county` | Yes | e.g. "Los Angeles" |
| `notice_type` | Yes | Raw string from portal |
| `source_url` | Yes | Portal URL scraped from |

### State-Specific Filter Notes

| State | Portal | Case Type Filter |
|---|---|---|
| California | courts.ca.gov | "Unlawful Detainer" |
| Texas (Harris) | jp.hctx.net | "Eviction" or "Forcible Detainer" |
| Texas (others) | Texas Justice Court statewide | "Eviction" or "Forcible Detainer" |
| Illinois | Illinois Circuit Court public search | "Eviction" or "Forcible Entry and Detainer" |
| Florida | Florida Courts E-Filing Portal | "Eviction" or "Unlawful Detainer" |
| Washington | Washington Courts public search | "Unlawful Detainer" |
| Arizona | AZ Judicial Branch case lookup | "Forcible Entry and Detainer" |
| Nevada | Nevada Courts case search | "Summary Eviction" or "Unlawful Detainer" |
| Georgia | Georgia Courts public access | **"Dispossessory"** — NOT "Unlawful Detainer". Using the wrong term returns zero results. |

### Build Order

Build and test California fully before touching any other state. Clone the California pattern for each subsequent state — only the portal URL, selectors, and case type filter change.

---

## Deduplication

Before enriching any filing, check Supabase `filings` table for `case_number`. If found, skip entirely. If not found, insert immediately (before enrichment) to prevent race conditions if the job runs twice.

Table: `filings`

| Column | Type | Notes |
|---|---|---|
| `case_number` | text | Primary key |
| `tenant_name` | text | |
| `property_address` | text | |
| `landlord_name` | text | |
| `filing_date` | date | |
| `court_date` | date | nullable |
| `state` | text | |
| `county` | text | |
| `notice_type` | text | |
| `source_url` | text | |
| `scraped_at` | timestamptz | auto |
| `enriched` | boolean | default false |
| `enriched_at` | timestamptz | nullable |
| `routed` | boolean | default false |
| `routing_outcome` | text | nullable — "proceed", "below_threshold", "commercial", "no_contact" |
| `ghl_contact_id` | text | nullable |
| `bland_triggered` | boolean | default false |

---

## BatchData Integration

### Request

```
POST https://api.batchdata.com/api/v1/...     ← verify exact endpoint from credentials
Authorization: Bearer {BATCHDATA_API_KEY}
Content-Type: application/json

{
  "firstName": "...",
  "lastName": "...",
  "address": "...",
  "city": "...",
  "state": "...",
  "zip": "..."
}
```

### Response Fields to Use

| BatchData field | Maps to |
|---|---|
| Primary cell phone | GHL contact phone + Bland.ai trigger |
| Email address | GHL contact email |
| Secondary address | GHL custom field |

### Error Handling (All Three Are Required)

| Scenario | Action |
|---|---|
| No phone returned | Tag `No-Phone` in GHL. Skip Bland.ai. Route to Instantly.ai email sequence if email available. |
| No email returned | Bland.ai + SMS only. |
| No phone AND no email | Tag `No-Contact`. Archive. Do not fire any outreach. |

### Cost Tracking

Every BatchData call writes a row to Supabase `batchdata_cost_log` table:

| Column | Type |
|---|---|
| `id` | uuid |
| `case_number` | text |
| `called_at` | timestamptz |
| `cost_usd` | numeric — always 0.07 |
| `phone_returned` | boolean |
| `email_returned` | boolean |

Budget: $847/month across both Grant Ellis Group and Vantage Defense Group combined.

---

## Routing Logic

After BatchData enrichment, apply this router before touching GHL:

```
if property_type == "commercial":
    tag: NG-New-Filing, Commercial
    pipeline: Vantage Defense Group Commercial
    priority: HIGH
    → proceed to GHL

elif property_type == "residential" and estimated_rent < 1800:
    tag: Below-Threshold
    → do NOT create GHL contact
    → do NOT fire outreach
    → Sunshine reviews weekly batch

elif property_type == "residential" and estimated_rent >= 1800:
    tag: EC-New-Filing
    pipeline: Grant Ellis Group
    → proceed to GHL pipeline
```

**Business assignment rule:**
- Residential filings → Grant Ellis Group (EC) only
- Commercial filings → Vantage Defense Group (NG) only
- Current enrichment can make one landlord skip-trace plus one property lookup per filing. When Vantage Defense Group tenant outreach is enabled for an individual tenant, it can also make one tenant people-search plus one additional property lookup. Optimize this before scaling if BatchData costs become a constraint.

**Note:** `estimated_rent` and `property_type` come from BatchData response. If BatchData does not return these fields, flag as open item — routing cannot function without them.

---

## GHL Integration

### Contact Creation

```
POST https://rest.gohighlevel.com/v1/contacts/
Authorization: Bearer {GHL_API_KEY}

{
  "firstName": "...",
  "lastName": "...",
  "phone": "...",
  "email": "...",
  "address1": "...",
  "tags": ["EC-New-Filing"],
  "customField": {
    "secondary_address": "...",
    "case_number": "...",
    "filing_date": "...",
    "court_date": "...",
    "landlord_name": "..."
  }
}
```

### Pipeline Stage

```
PUT https://rest.gohighlevel.com/v1/opportunities/{opportunity_id}
{ "stageId": "{GHL_NEW_FILING_STAGE_ID}" }
```

**Stage IDs are not guessable — must be retrieved from GHL sub-account.** See open items.

### GHL API Notes

- Verify v1 vs v2 endpoints before implementing — requirements reference v1 but GHL has been rolling out v2
- Auth: `Authorization: Bearer {GHL_API_KEY}`
- If GHL contact creation fails, log error but do NOT retry automatically on first build — flag for manual review

---

## Bland.ai Integration

Fire immediately after GHL contact is created, only if phone number is available.

```
POST https://api.bland.ai/v1/calls
Authorization: {BLAND_API_KEY}

{
  "phone_number": "...",
  "from": "{BLAND_PHONE_NUMBER}",
  "task": "...",           ← voicemail script, defined by Bland agent config
  "voice_id": "...",
  "request_data": {
    "tenant_name": "...",
    "property_address": "..."
  }
}
```

Bland agent scripts are configured separately in the Bland.ai dashboard. This service only triggers the call — it does not define the script.

After Bland fires, GHL's native workflow takes over:
- SMS fires 2 hours after contact creation
- Follow-up sequences run per GHL workflow configuration

---

## Environment Variables

```bash
# BatchData
BATCHDATA_API_KEY=

# GHL
GHL_API_KEY=
GHL_LOCATION_ID=
GHL_NEW_FILING_STAGE_ID=          # EC pipeline "New Filing" stage
GHL_NG_COMMERCIAL_STAGE_ID=       # Vantage Defense Group Commercial pipeline stage

# Bland.ai
BLAND_API_KEY=
BLAND_PHONE_NUMBER=               # Outbound number registered in Bland

# Supabase
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=        # Service role — full DB access for backend use

# Service
ENVIRONMENT=production
LOG_LEVEL=INFO
```

---

## nixpacks.toml (Required for Playwright on Railway)

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

---

## Railway Cron Configuration

Each state scraper is a separate cron job in `railway.toml`. California runs first while other states are being built.

```toml
[[cron]]
schedule = "0 6 * * *"
command = "python jobs/run_california.py"

# Add each state as it's built and tested:
# [[cron]]
# schedule = "0 6 * * *"
# command = "python jobs/run_texas.py"
```

---

## Build and Test Order

Do not move to the next item until the current one is confirmed working end-to-end.

1. **California scraper** — LA, San Diego, Orange, Riverside counties. Test with real portal data.
2. **Dedup service** — Supabase table + insert/check logic
3. **BatchData service** — real API call with test tenant name/address. Verify all three error handling paths.
4. **Router** — unit test all routing branches with mock data
5. **GHL service** — create contact, set tag, set pipeline stage. Verify in GHL UI.
6. **Bland.ai service** — trigger real test call to a test number
7. **End-to-end test** — 5 dummy California filings through the full pipeline
8. **Texas scraper** — only after California is confirmed live
9. **All remaining states** — one at a time, clone California pattern

---

## Open Items (Do Not Build Until Resolved)

| Item | Status | Who |
|---|---|---|
| BatchData API credentials | Sunshine sending today | Sunshine |
| BatchData exact endpoint + response schema | Blocked until credentials arrive | Dev |
| GHL New Filing stage ID (EC pipeline) | Need from GHL sub-account | Zee |
| GHL Commercial stage ID (NG pipeline) | Need from GHL sub-account | Zee |
| GHL custom field IDs for case_number, filing_date, etc. | Need from GHL sub-account | Zee |
| Business assignment logic (EC vs NG) | **RESOLVED** — Residential → EC, Commercial → NG | — |
| `estimated_rent` and `property_type` from BatchData | Confirm BatchData returns these — routing depends on them | Dev (verify with credentials) |
| Bland.ai agent IDs for each business | Configured in Bland dashboard | Zee |
| Bland outbound phone number(s) | Zee to confirm | Zee |
| GHL API v1 vs v2 verification | Verify correct endpoints before implementing GHL calls | Dev |

---

## Testing Checklist (Before Going Live)

- [ ] California scraper returns filings in correct `Filing` shape
- [ ] Duplicate case_number is skipped, not re-enriched
- [ ] BatchData call fires with correct fields, response parsed correctly
- [ ] No-phone path: `No-Phone` tag applied, Bland.ai NOT triggered
- [ ] No-email path: Bland.ai fires, no email sequence
- [ ] No-phone + no-email path: `No-Contact` tag, nothing else fires
- [ ] Below-threshold routing: contact NOT created in GHL
- [ ] Commercial routing: contact goes to NG Commercial pipeline with HIGH PRIORITY
- [ ] Residential $1,800+ routing: contact created in EC pipeline, tagged, Bland fires
- [ ] GHL contact has all custom fields populated
- [ ] Bland.ai voicemail confirmed delivered to test number
- [ ] BatchData cost log row written for every call
- [ ] Full run with 5 dummy contacts before anything touches real leads

---

## Local Dev Setup

```bash
pip install -r requirements.txt
playwright install chromium

cp .env.example .env
# Fill in all env vars

# Run a single scraper manually for testing
python jobs/run_california.py

# Run tests
pytest tests/ -v
```

---

*Last updated: 2026-04-30*
*Owner: Zee*
*Client: Grant Ellis Group / Vantage Defense Group*
