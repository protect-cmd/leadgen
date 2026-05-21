# Vantage Go-Live Data Plan — 2026-05-18

## Executive Recommendation

Do **not** try to enrich every approved tenant filing before launch.

For go-live, use a strict **Green-A** lane:

- fresh / still-actionable
- residential approved
- real street address
- clean human tenant name
- no obvious junk markers
- no existing tenant phone

Current live inventory:

| Metric | Count |
|---|---:|
| Live approved tenant filings | 225 |
| Green-A enrichment candidates | 192 |
| Live approved already with tenant phone | 17 |
| Live approved callable now | 16 |

The immediate bottleneck is **activation**, not source discovery.

## Exact Green-A Rule

A filing is eligible for automatic paid tenant enrichment only when all are true:

1. `lead_bucket = 'residential_approved'`
2. `court_date IS NULL OR court_date >= CURRENT_DATE`
3. `property_address` starts with a street number
4. `tenant_name` is not `Unknown`
5. `tenant_name` is not a business/entity name
6. `tenant_name` does not contain obvious noisy markers such as:
   - `AKA`
   - `OCCUPANT`
7. there is no existing NG/tenant phone already stored

Reference SQL:

```sql
with candidates as (
  select
    f.*,
    lc.phone as ng_phone,
    case when f.property_address ~ '^[0-9]' then true else false end as has_street_address,
    case when lower(trim(f.tenant_name)) = 'unknown' then true else false end as unknown_tenant,
    case when f.tenant_name ~* '\b(llc|inc|corp|ltd|lp|llp|properties|property|management|mgmt|realty|investments|holdings|trust|partners|group|enterprises|ventures)\b'
      then true else false end as business_tenant,
    case when f.tenant_name ~* 'aka|occupant' then true else false end as noisy_tenant,
    case when f.court_date is not null and f.court_date < current_date then true else false end as court_overdue
  from public.filings f
  left join public.lead_contacts lc
    on lc.case_number = f.case_number
   and lc.track = 'ng'
)
select *
from candidates
where lead_bucket = 'residential_approved'
  and not court_overdue
  and has_street_address
  and not unknown_tenant
  and not business_tenant
  and not noisy_tenant
  and ng_phone is null;
```

## Current Inventory Snapshot

### Live approved inventory by county

| County | Live approved | Green-A candidates |
|---|---:|---:|
| Harris, TX | 128 | 114 |
| Davidson, TN | 85 | 66 |
| Franklin, OH | 10 | 10 |
| Maricopa, AZ | 2 | 2 |

### Quality breakdown of current live approved inventory

| Category | Count |
|---|---:|
| Live approved total | 225 |
| Green-A clean candidates | 192 |
| Unknown tenant names | 4 |
| Noisy tenant strings (`AKA` / `OCCUPANT`) | 29 |
| Already with tenant phone | 17 |
| Already callable | 16 |

## First 25 Leads To Enrich Next

Priority rule used:

1. not past court date
2. live residential approved
3. clean tenant string
4. no current tenant phone
5. earliest court date first

| Case # | Tenant | County | Court Date |
|---|---|---|---|
| 25GT11484 | VICTORIA JOHNSON | Davidson | 2026-05-18 |
| 25GT12891 | ZARIYAH DULANEY | Davidson | 2026-05-18 |
| 26GT1309 | ABDELRAHMAN MOHAMOUD | Davidson | 2026-05-18 |
| 26GT1910 | SHAUNTRAIL PICKETT | Davidson | 2026-05-18 |
| 26GT2234 | KENNY HARDY | Davidson | 2026-05-18 |
| 26GT2325 | DENIS WEBER | Davidson | 2026-05-18 |
| 26GT2590 | JOHNATHAN LEWIS | Davidson | 2026-05-18 |
| 26GT3351 | RASHOD ROSE | Davidson | 2026-05-18 |
| 26GT4786 | BRITTANY HARLESTON | Davidson | 2026-05-18 |
| 26GT4856 | SAMANTHA HUBAY | Davidson | 2026-05-18 |
| 26GT4916 | DANIELLE ROBINSON | Davidson | 2026-05-18 |
| 26GT4938 | ADRIA DOANE | Davidson | 2026-05-18 |
| 26GT4939 | JAYLIN PREWITT | Davidson | 2026-05-18 |
| 26GT4963 | JOSE ARGUELLO | Davidson | 2026-05-18 |
| 26GT4964 | DARIUS BOYD | Davidson | 2026-05-18 |
| 26GT4965 | DANIEL DUGAN | Davidson | 2026-05-18 |
| 26GT4969 | DOROTHEA WILLIAMS | Davidson | 2026-05-18 |
| 26GT4971 | KALIYAH PORTER | Davidson | 2026-05-18 |
| 26GT4992 | ALEXANDER CROMARTIE | Davidson | 2026-05-18 |
| 26GT4999 | RUSSELL A LYLES | Davidson | 2026-05-18 |
| 26GT5000 | DEVONTE BOONE | Davidson | 2026-05-18 |
| 26GT5001 | SHADA MORRIS | Davidson | 2026-05-18 |
| 26GT5011 | SHARON SPENCER | Davidson | 2026-05-18 |
| 26GT5012 | TIMMESHIA FLEMING | Davidson | 2026-05-18 |
| 26GT5014 | JAIDEN WASHINGTON | Davidson | 2026-05-18 |

## Recommended First-Pass Dashboard Changes

The Vantage dashboard should answer two questions first:

1. **What can I call now?**
2. **What should I enrich next?**

### Replace / supplement the top metrics with

| Metric | Meaning |
|---|---|
| `Live Approved` | approved tenant filings not past court date |
| `Green-A Eligible` | worth paying to enrich |
| `Needs Enrichment` | Green-A eligible with no tenant phone |
| `Callable Now` | phone present + DNC clear + not overdue |
| `Overdue` | should not dominate the working queue |

### Change the Vantage residential default view

Default to:

- not overdue
- residential approved
- tenant track

Add a quick chip for:

- `Needs Enrichment`
- `Callable Now`

### Add useful row-level signals

| Signal | Why |
|---|---|
| `Eligibility` badge: `Green-A`, `Review`, `Exclude` | clarifies whether paid enrichment should happen |
| `Court status`: `Today`, `Upcoming`, `Past` | prevents wasted focus |
| `Tenant quality reason` | e.g. `Unknown tenant`, `Occupant marker`, `Business name` |
| `Enrichment status` | `Not Enriched`, `Phone Found`, `Callable`, `Review` |

## What To Do Today

1. Define Green-A eligibility in code / SQL.
2. Run a **first batch of 25** Green-A enrichments with SearchBug.
3. Inspect:
   - phone hit rate
   - exact address match rate
   - different-address rate
   - DNC-clear rate
   - cost per callable tenant
4. If the batch is acceptable, continue in chunks of 25.
5. Update the Vantage dashboard so live usable inventory is visible immediately.

## What Not To Do Today

- Do not enrich all 192 blindly.
- Do not spend on noisy / stale rows.
- Do not let overdue filings remain mixed into the default working queue.
- Do not spend time integrating a second vendor before the first launch batch is measured.

## Later / Do Not Forget

After the go-live push, return to the previously identified tenant-first remediation work:

1. **Route yellow leads into the production runner**
2. **Make green SearchBug fallback explicit and mismatch-only**
3. **Promote recovered yellow addresses into downstream filing data**
4. **Refresh `docs/tenant-first-enrichment-summary.md` after those fixes**

Related implementation plan already drafted:

- `docs/superpowers/plans/2026-05-18-tenant-first-remediation.md`

