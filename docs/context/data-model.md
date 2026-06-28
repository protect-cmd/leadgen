# Data Model (Supabase / Postgres)

Per-business tables — **no unified table** (deliberate; see [[decisions]]). Reads/writes go
through `services/dedup_service.py` (Vantage) and `cd_store.py` / `gp_store.py` (Cosner/GP).
`case_number` is the PRIMARY KEY on each filings-style table.

## Tables
| Table | Business | Lead person col | Address col | Freshness col | Amount |
|---|---|---|---|---|---|
| `filings` | Vantage | `tenant_name` | `property_address` | `filing_date` | `back_rent_total`/`estimated_rent` |
| `lead_contacts` | Vantage enrichment | `contact_name` | `secondary_address` | `enriched_at` | `estimated_rent` |
| `ists_judgments` | ISTS | `defendant_name` | `property_address` | `judgment_date` | `estimated_rent` |
| `cosner_filings` | Cosner | `defendant_name` | `defendant_address` | `filing_date` | `debt_amount` (+`amount_kind`) |
| `garnishment_orders` | Garnish Proof | `debtor_name` | `debtor_address` | `filing_date` (=writ) | — (`exemption_deadline`) |
| `quota_ledger` | spend guard | — | — | `day` | per-business reservations |
| `run_metrics` | telemetry | — | — | `run_at` | scrape/run counters |
| `priority_zips` | scoring | — | — | — | ZIP queue rank/metro |
| `lead_notes` | dashboard | — | — | `created_at` | caller notes |

`lead_contacts` keyed on `(case_number, track)` where `track` ∈ `ec` (landlord/Grant Ellis)
| `ng` (tenant/Vantage). FK: `lead_contacts.case_number → filings.case_number`.

## DNC enum (unified — Phase 4)
`dnc_status` ∈ **`callable` | `dnc` | `unknown`** (set by `services/dnc_service.py::verdict`).
The To-Fire queue + fire path require `callable`. (Legacy `clear`/`blocked` appear only in old
dashboard JS via a back-compat mapper `dncIsCallable`/`dncIsBlocked`; canonical is callable/dnc.)

## `good_leads_now` (view)
The Vantage "ready to enrich" pool: enrichable filings (passed gates, not yet enriched, fresh).
`queue_builder.build_to_enrich` reads it + scores. ISTS has `build_ists_to_enrich`. A
`lead_contacts` row with `phone IS NOT NULL OR enriched_at IS NOT NULL` suppresses re-enrichment
(don't re-pay a dead lookup).

## Dedup & data-loss notes (see [[decisions]])
- `is_duplicate(case_number)` checks `filings` globally; in practice safe because each county
  uses a **distinct case_number format** (verified — zero cross-county collisions), so the
  composite-key migration was **deferred** (risk without benefit).
- Upserts: `cd_store`/`gp_store` write only source columns (enrichment preserved on re-scrape);
  `lead_contacts` enrichment writes guard `enriched_at` to only stamp on real outcomes.

## Migrations
`migrations/NNN_*.sql`, applied to Supabase **manually / via MCP** (not auto on deploy — a
committed migration file is NOT necessarily applied; verify columns exist). Notable:
`023/024` garnishment_orders, `025` cosner_filings, `026` cosner debt_amount, `028` quota_ledger.

## Approx volumes (as of 2026-06-28)
filings ~18.7k, ists_judgments ~3.2k, cosner_filings ~1k, garnishment_orders 80 (manual import).
