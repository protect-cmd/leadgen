# Dashboard Brand Views Redesign — Design Spec
**Date:** 2026-05-11
**Status:** Approved

---

## Goal

Redesign the lead queue dashboard so Grant Ellis Group (EC) and Vantage Defense Group (NG) leads are clearly separated, the right contact data surfaces per brand, and the most actionable leads are front and center when you open the page.

---

## 1. Brand Tab Navigation

Two brand tabs sit in the toolbar **before** the view chips, separated from them by a divider.

```
Brand  [● GRANT]  [VANTAGE]  |  View  [Residential (223)]  [Commercial (1)] ...  |  Filter  [Has Phone]  [DNC Clear]  [Overdue]
```

- **GRANT** tab: amber active state (`--amber`, `--amber-dim` background)
- **VANTAGE** tab: blue active state (`#4a9bbf`, `#1a2d3a` background)
- Switching brand reloads view chip counts and the lead table. The active view chip resets to the first view of the selected brand.
- Header title stays **"GEG · LEAD QUEUE"** on both tabs — no header change.
- APPROVE button stays amber on both tabs.

---

## 2. Sub-Views Per Brand (Asymmetric)

**Grant (EC)** — 4 views:
| View key | Label | Filter logic |
|---|---|---|
| `ec_residential` | Residential | `lead_bucket = residential_approved`, non-Spanish, EC track |
| `ec_commercial` | Commercial | `lead_bucket = commercial`, EC track |
| `ec_held` | Held | `lead_bucket = held`, EC track |
| `ec_discarded` | Discarded | `lead_bucket = discarded`, EC track |

**Vantage (NG)** — 6 views:
| View key | Label | Filter logic |
|---|---|---|
| `ng_residential` | Residential | `lead_bucket = residential_approved`, non-Spanish, NG track |
| `ng_commercial` | Commercial | `lead_bucket = commercial`, NG track |
| `ng_spanish_residential` | Spanish Res | `lead_bucket = residential_approved`, `language_hint = spanish_likely`, NG track |
| `ng_spanish_commercial` | Spanish Com | `lead_bucket = commercial`, `language_hint = spanish_likely`, NG track |
| `ng_held` | Held | `lead_bucket = held`, NG track |
| `ng_discarded` | Discarded | `lead_bucket = discarded`, NG track |

Spanish sub-views only appear on the Vantage tab. Language distinction only matters when calling the tenant.

---

## 3. Column Layout Per Brand

Both tabs share the same column structure with two differences: **Est. Rent** (Grant only) swaps for **Language badge** (Vantage only).

### Grant (EC) columns

| Column | Content | Notes |
|---|---|---|
| ☐ | Checkbox | Bulk select |
| Case # | `case_number` | Amber mono |
| Tenant | `tenant_name` + county/state sub-line | Context — who was evicted |
| **Landlord ★** | `landlord_name` bold | **Target contact** |
| Address | `property_address` + zip sub-line | |
| **Phone ★** | Landlord phone from EC `lead_contacts` | **Target number** |
| DNC | `dnc_status` badge | clear=green, blocked=red, unknown=gray |
| Est. Rent | `estimated_rent` | Property value signal — only on Grant |
| Type | `property_type` badge | residential / commercial |
| Court Date ↑ | `court_date` | Default sort ascending |
| Filed | `filing_date` | |
| Action | Approve / Skip / DNC Clear / status label | |

**Additional indicators on Grant rows:**
- **Email dot** — small `✉` indicator if EC `lead_contacts.email` is not null (= enrolled in Instantly EC campaign)
- **Corporate flag** — `CORP` badge on the Landlord cell when landlord name matches business terms (LLC, INC, CORP, LP, LLP, TRUST, PROPERTIES, MANAGEMENT, REALTY, GROUP, HOLDINGS, ENTERPRISES). This is a visual warning that skip-trace phone quality may be lower — it does not block Approve.

### Vantage (NG) columns

| Column | Content | Notes |
|---|---|---|
| ☐ | Checkbox | Bulk select |
| Case # | `case_number` | Amber mono |
| **Tenant ★** | `tenant_name` bold + county/state sub-line | **Target contact** |
| Landlord | `landlord_name` sub-text | Context — who filed |
| Address | `property_address` + zip sub-line | |
| **Phone ★** | Tenant phone from NG `lead_contacts` | **Target number** |
| DNC | `dnc_status` badge | clear=green, blocked=red, unknown=gray |
| Language | EN / ES badge | Only on Vantage — drives Bland script selection |
| Type | `property_type` badge | residential / commercial |
| Court Date ↑ | `court_date` | Default sort ascending — critical for defense |
| Filed | `filing_date` | |
| Action | Approve / Skip / DNC Clear / status label | |

**Additional indicators on Vantage rows:**
- **Email dot** — small `✉` indicator if NG `lead_contacts.email` is not null (= enrolled in Instantly NG campaign)
- No corporate flag needed — tenant name matching already validates the phone belongs to a person

**Language badge values:**
- `ES` — `language_hint = spanish_likely` — blue badge, triggers NG Spanish Bland script
- `EN` — all other — gray/dim, triggers NG English Bland script

---

## 4. Metrics Strip

Add a **Ready to Call** count as a highlighted metric, positioned after Pending:

```
[339 PENDING]  [47 READY TO CALL ← green]  [225 LAST SCRAPED]  [225 DUPES SKIPPED]  ...
```

- **Ready to Call** = count of currently loaded leads where `phone` is not null AND `dnc_status = 'clear'`
- Computed client-side from `allLeads` after each load — no extra API call
- Green color (`--green`) to draw the eye immediately
- Reacts to brand/view switches automatically since it's derived from loaded data

---

## 5. Default Sort + NEW Badge

**Default sort:** `court_date ASC` (soonest hearing first). Changed from current `filing_date DESC`.

- Rationale: upcoming hearings are time-sensitive. Leads with court dates already passed stay in the list — the existing **Overdue** filter chip hides them when you want to focus on upcoming cases.
- Sort is still user-overridable by clicking column headers.

**NEW badge:**
- A small `NEW` badge appears in the tenant name cell on rows where `scraped_at` is within the last scrape window (i.e., `scraped_at >= last run_at` from the most recent run metrics row)
- `scraped_at` is already on the `filings` table
- `run_at` is already available from `/api/metrics` (loaded on every refresh)
- Computed client-side: after `loadMetrics()` returns `lastRunAt`, mark rows where `lead.scraped_at >= lastRunAt`
- Blue pill badge (`#1a2d3a` background, `#4a9bbf` text) — subtle, doesn't compete with action buttons

---

## 6. Filter Chips

Filter chips are **shared across both brand tabs** — they apply to whichever view is active:

| Chip | Behaviour |
|---|---|
| Has Phone | `phone != null` |
| DNC Clear | `dnc_status = 'clear'` |
| Residential | `property_type = 'residential'` |
| Commercial | `property_type = 'commercial'` |
| Overdue | `court_date < today` — filters to only overdue leads; use to review or bulk-skip past-hearing leads |

No new filter chips added in this redesign.

---

## 7. Backend Changes

### `/api/lead-counts`
Return counts keyed by new view keys:
```json
{
  "ec_residential": 223, "ec_commercial": 1, "ec_held": 12, "ec_discarded": 761,
  "ng_residential": 180, "ng_commercial": 1, "ng_spanish_residential": 3,
  "ng_spanish_commercial": 0, "ng_held": 12, "ng_discarded": 761
}
```
`get_dashboard_counts()` in `dedup_service.py` must be updated to separate EC vs NG counts per bucket.

### `/api/leads`
`GET /api/leads?view=ec_residential` — view key now encodes both the bucket and the track.  
`_filter_dashboard_query()` and `_track_for_dashboard_view()` updated to handle new view keys.  
The `track` query param remains for explicit override.

### Default sort in `get_dashboard_leads`
Change `.order("filing_date", desc=True)` → `.order("court_date", desc=False, nulls_last=True)`.  
Nulls last so leads with no court date don't crowd the top.

### `scraped_at` in dashboard select
Add `scraped_at` to `_DASHBOARD_SELECT` so the NEW badge logic has data to work with.

---

## 8. What Does NOT Change

- Header title and logo — stays "GEG · LEAD QUEUE" on both tabs
- APPROVE button color — amber on both tabs
- Bulk approve, bulk skip, keyboard shortcuts (j/k/a/s/space)
- DNC clear flow
- Bland QA test call endpoints (if added later)
- `filings` table schema — no migrations needed for this redesign
- `lead_contacts` table schema — no migrations needed

---

## 9. Out of Scope

- Per-lead `instantly_enrolled` flag — requires a new `lead_contacts` column and migration; tracked as future work
- Phone type (mobile vs landline) indicator — requires storing `phone_type` from BatchData response; tracked as future work
- EC landlord name validation (equivalent to NG's `_tenant_name_matches`) — separate backend improvement, not part of this dashboard redesign
